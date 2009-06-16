import traceback

from mercurial import revlog
from mercurial import node
from mercurial import context
from mercurial import util as hgutil

import svnexternals
import util


class MissingPlainTextError(Exception):
    """Exception raised when the repo lacks a source file required for replaying
    a txdelta.
    """

class ReplayException(Exception):
    """Exception raised when you try and commit but the replay encountered an
    exception.
    """

def convert_rev(ui, meta, svn, r, tbdelta):
    # ui is only passed in for similarity with stupid.convert_rev()
    hg_editor = meta.editor
    hg_editor.current.clear()
    hg_editor.current.rev = r
    meta.save_tbdelta(tbdelta) # needed by get_replay()
    svn.get_replay(r.revnum, meta.editor)
    i = 1
    if hg_editor.current.missing:
        meta.ui.debug('Fetching %s files that could not use replay.\n' %
                      len(hg_editor.current.missing))
        files_to_grab = set()
        rootpath = svn.subdir and svn.subdir[1:] or ''
        for p in hg_editor.current.missing:
            meta.ui.note('.')
            meta.ui.flush()
            if p[-1] == '/':
                dirpath = p[len(rootpath):]
                files_to_grab.update([dirpath + f for f,k in
                                      svn.list_files(dirpath, r.revnum)
                                      if k == 'f'])
            else:
                files_to_grab.add(p[len(rootpath):])
        meta.ui.note('\nFetching files...\n')
        for p in files_to_grab:
            meta.ui.note('.')
            meta.ui.flush()
            if i % 50 == 0:
                svn.init_ra_and_client()
            i += 1
            data, mode = svn.get_file(p, r.revnum)
            hg_editor.current.set(p, data, 'x' in mode, 'l' in mode)
        hg_editor.current.missing = set()
        meta.ui.note('\n')
    _updateexternals(meta, hg_editor.current)
    return commit_current_delta(meta, tbdelta, hg_editor.current)


def _updateexternals(meta, current):
    if not current.externals:
        return
    # Accumulate externals records for all branches
    revnum = current.rev.revnum
    branches = {}
    for path, entry in current.externals.iteritems():
        if not meta.is_path_valid(path):
            meta.ui.warn('WARNING: Invalid path %s in externals\n' % path)
            continue
        p, b, bp = meta.split_branch_path(path)
        if bp not in branches:
            external = svnexternals.externalsfile()
            parent = meta.get_parent_revision(revnum, b)
            pctx = meta.repo[parent]
            if '.hgsvnexternals' in pctx:
                external.read(pctx['.hgsvnexternals'].data())
            branches[bp] = external
        else:
            external = branches[bp]
        external[p] = entry

    # Register the file changes
    for bp, external in branches.iteritems():
        path = bp + '/.hgsvnexternals'
        if external:
            current.set(path, external.write(), False, False)
        else:
            current.delete(path)


def commit_current_delta(meta, tbdelta, current):

    if current.exception is not None:  #pragma: no cover
        traceback.print_exception(*current.exception)
        raise ReplayException()
    if current.missing:
        raise MissingPlainTextError()

    # paranoidly generate the list of files to commit
    files_to_commit = set(current.files.keys())
    files_to_commit.update(current.symlinks.keys())
    files_to_commit.update(current.execfiles.keys())
    files_to_commit.update(current.deleted.keys())
    # back to a list and sort so we get sane behavior
    files_to_commit = list(files_to_commit)
    files_to_commit.sort()
    branch_batches = {}
    rev = current.rev
    date = meta.fixdate(rev.date)

    # build up the branches that have files on them
    for f in files_to_commit:
        if not meta.is_path_valid(f):
            continue
        p, b = meta.split_branch_path(f)[:2]
        if b not in branch_batches:
            branch_batches[b] = []
        branch_batches[b].append((p, f))

    closebranches = {}
    for branch in tbdelta['branches'][1]:
        branchedits = meta.revmap.branchedits(branch, rev)
        if len(branchedits) < 1:
            # can't close a branch that never existed
            continue
        ha = branchedits[0][1]
        closebranches[branch] = ha

    # 1. handle normal commits
    closedrevs = closebranches.values()
    for branch, files in branch_batches.iteritems():

        if branch in current.emptybranches and files:
            del current.emptybranches[branch]

        files = dict(files)
        parents = meta.get_parent_revision(rev.revnum, branch), revlog.nullid
        if parents[0] in closedrevs and branch in meta.closebranches:
            continue

        extra = meta.genextra(rev.revnum, branch)
        if branch is not None:
            if (branch not in meta.branches
                and branch not in meta.repo.branchtags()):
                continue

        parent_ctx = meta.repo.changectx(parents[0])
        if '.hgsvnexternals' not in parent_ctx and '.hgsvnexternals' in files:
            # Do not register empty externals files
            if (files['.hgsvnexternals'] in current.files
                and not current.files[files['.hgsvnexternals']]):
                del files['.hgsvnexternals']

        def filectxfn(repo, memctx, path):
            current_file = files[path]
            if current_file in current.deleted:
                raise IOError()
            copied = current.copies.get(current_file)
            flags = parent_ctx.flags(path)
            is_exec = current.execfiles.get(current_file, 'x' in flags)
            is_link = current.symlinks.get(current_file, 'l' in flags)
            if current_file in current.files:
                data = current.files[current_file]
                if is_link and data.startswith('link '):
                    data = data[len('link '):]
                elif is_link:
                    meta.ui.warn('file marked as link, but contains data: '
                                 '%s (%r)\n' % (current_file, flags))
            else:
                data = parent_ctx.filectx(path).data()
            return context.memfilectx(path=path,
                                      data=data,
                                      islink=is_link, isexec=is_exec,
                                      copied=copied)

        if not meta.usebranchnames:
            extra.pop('branch', None)
        current_ctx = context.memctx(meta.repo,
                                     parents,
                                     rev.message or '...',
                                     files.keys(),
                                     filectxfn,
                                     meta.authors[rev.author],
                                     date,
                                     extra)

        new_hash = meta.repo.commitctx(current_ctx)
        util.describe_commit(meta.ui, new_hash, branch)
        if (rev.revnum, branch) not in meta.revmap:
            meta.revmap[rev.revnum, branch] = new_hash

    # 2. handle branches that need to be committed without any files
    for branch in current.emptybranches:

        ha = meta.get_parent_revision(rev.revnum, branch)
        if ha == node.nullid:
            continue

        parent_ctx = meta.repo.changectx(ha)
        def del_all_files(*args):
            raise IOError

        # True here meant nuke all files, shouldn't happen with branch closing
        if current.emptybranches[branch]: #pragma: no cover
            raise hgutil.Abort('Empty commit to an open branch attempted. '
                               'Please report this issue.')

        extra = meta.genextra(rev.revnum, branch)
        if not meta.usebranchnames:
            extra.pop('branch', None)

        current_ctx = context.memctx(meta.repo,
                                     (ha, node.nullid),
                                     rev.message or ' ',
                                     [],
                                     del_all_files,
                                     meta.authors[rev.author],
                                     date,
                                     extra)
        new_hash = meta.repo.commitctx(current_ctx)
        util.describe_commit(meta.ui, new_hash, branch)
        if (rev.revnum, branch) not in meta.revmap:
            meta.revmap[rev.revnum, branch] = new_hash

    return closebranches
