import cStringIO
import re

from mercurial import patch
from mercurial import node
from mercurial import context
from mercurial import revlog
from svn import core

import svnwrap
import svnexternals
import util


binary_file_re = re.compile(r'''Index: ([^\n]*)
=*
Cannot display: file marked as a binary type.''')

property_exec_set_re = re.compile(r'''Property changes on: ([^\n]*)
_*
(?:Added|Name): svn:executable
   \+''')

property_exec_removed_re = re.compile(r'''Property changes on: ([^\n]*)
_*
(?:Deleted|Name): svn:executable
   -''')

empty_file_patch_wont_make_re = re.compile(r'''Index: ([^\n]*)\n=*\n(?=Index:)''')

any_file_re = re.compile(r'''^Index: ([^\n]*)\n=*\n''', re.MULTILINE)

property_special_set_re = re.compile(r'''Property changes on: ([^\n]*)
_*
(?:Added|Name): svn:special
   \+''')

property_special_removed_re = re.compile(r'''Property changes on: ([^\n]*)
_*
(?:Deleted|Name): svn:special
   \-''')


class BadPatchApply(Exception):
    pass


def print_your_svn_is_old_message(ui): #pragma: no cover
    ui.status("In light of that, I'll fall back and do diffs, but it won't do "
              "as good a job. You should really upgrade your server.\n")


def mempatchproxy(parentctx, files):
    # Avoid circular references patch.patchfile -> mempatch
    patchfile = patch.patchfile

    class mempatch(patchfile):
        def __init__(self, ui, fname, opener, missing=False):
            patchfile.__init__(self, ui, fname, None, False)

        def readlines(self, fname):
            if fname not in parentctx:
                raise IOError('Cannot find %r to patch' % fname)
            fctx = parentctx[fname]
            data = fctx.data()
            if 'l' in fctx.flags():
                data = 'link ' + data
            return cStringIO.StringIO(data).readlines()

        def writelines(self, fname, lines):
            files[fname] = ''.join(lines)

        def unlink(self, fname):
            files[fname] = None

    return mempatch


def filteriterhunks(hg_editor):
    iterhunks = patch.iterhunks
    def filterhunks(ui, fp, sourcefile=None):
        applycurrent = False
        for data in iterhunks(ui, fp, sourcefile):
            if data[0] == 'file':
                if hg_editor._is_file_included(data[1][1]):
                    applycurrent = True
                else:
                    applycurrent = False
            assert data[0] != 'git', 'Filtering git hunks not supported.'
            if applycurrent:
                yield data
    return filterhunks


def diff_branchrev(ui, svn, hg_editor, branch, r, parentctx):
    """Extract all 'branch' content at a given revision.

    Return a tuple (files, filectxfn) where 'files' is the list of all files
    in the branch at the given revision, and 'filectxfn' is a memctx compatible
    callable to retrieve individual file information. Raise BadPatchApply upon
    error.
    """
    def make_diff_path(branch):
        if branch == 'trunk' or branch is None:
            return 'trunk'
        elif branch.startswith('../'):
            return branch[3:]
        return 'branches/%s' % branch
    parent_rev, br_p = hg_editor.get_parent_svn_branch_and_rev(r.revnum, branch)
    diff_path = make_diff_path(branch)
    try:
        if br_p == branch:
            # letting patch handle binaries sounded
            # cool, but it breaks patch in sad ways
            d = svn.get_unified_diff(diff_path, r.revnum, deleted=False,
                                     ignore_type=False)
        else:
            d = svn.get_unified_diff(diff_path, r.revnum,
                                     other_path=make_diff_path(br_p),
                                     other_rev=parent_rev,
                                     deleted=True, ignore_type=True)
            if d:
                raise BadPatchApply('branch creation with mods')
    except svnwrap.SubversionRepoCanNotDiff:
        raise BadPatchApply('subversion diffing code is not supported')
    except core.SubversionException, e:
        if (hasattr(e, 'apr_err') and e.apr_err != core.SVN_ERR_FS_NOT_FOUND):
            raise
        raise BadPatchApply('previous revision does not exist')
    if '\0' in d:
        raise BadPatchApply('binary diffs are not supported')
    files_data = {}
    binary_files = {}
    touched_files = {}
    for m in binary_file_re.findall(d):
        # we have to pull each binary file by hand as a fulltext,
        # which sucks but we've got no choice
        binary_files[m] = 1
        touched_files[m] = 1
    d2 = empty_file_patch_wont_make_re.sub('', d)
    d2 = property_exec_set_re.sub('', d2)
    d2 = property_exec_removed_re.sub('', d2)
    for f in any_file_re.findall(d):
        # Here we ensure that all files, including the new empty ones
        # are marked as touched. Content is loaded on demand.
        touched_files[f] = 1
    if d2.strip() and len(re.findall('\n[-+]', d2.strip())) > 0:
        try:
            oldpatchfile = patch.patchfile
            olditerhunks = patch.iterhunks
            patch.patchfile = mempatchproxy(parentctx, files_data)
            patch.iterhunks = filteriterhunks(hg_editor)
            try:
                # We can safely ignore the changed list since we are
                # handling non-git patches. Touched files are known
                # by our memory patcher.
                patch_st = patch.applydiff(ui, cStringIO.StringIO(d2),
                                           {}, strip=0)
            finally:
                patch.patchfile = oldpatchfile
                patch.iterhunks = olditerhunks
        except patch.PatchError:
            # TODO: this happens if the svn server has the wrong mime
            # type stored and doesn't know a file is binary. It would
            # be better to do one file at a time and only do a
            # full fetch on files that had problems.
            raise BadPatchApply('patching failed')
        for x in files_data.iterkeys():
            ui.note('M  %s\n' % x)
        # if this patch didn't apply right, fall back to exporting the
        # entire rev.
        if patch_st == -1:
            assert False, ('This should only happen on case-insensitive'
                           ' volumes.')
        elif patch_st == 1:
            # When converting Django, I saw fuzz on .po files that was
            # causing revisions to end up failing verification. If that
            # can be fixed, maybe this won't ever be reached.
            raise BadPatchApply('patching succeeded with fuzz')
    else:
        ui.status('Not using patch for %s, diff had no hunks.\n' %
                  r.revnum)

    exec_files = {}
    for m in property_exec_removed_re.findall(d):
        exec_files[m] = False
    for m in property_exec_set_re.findall(d):
        exec_files[m] = True
    for m in exec_files:
        touched_files[m] = 1
    link_files = {}
    for m in property_special_set_re.findall(d):
        # TODO(augie) when a symlink is removed, patching will fail.
        # We're seeing that above - there's gotta be a better
        # workaround than just bailing like that.
        assert m in files_data
        link_files[m] = True
    for m in property_special_removed_re.findall(d):
        assert m in files_data
        link_files[m] = False

    for p in r.paths:
        if p.startswith(diff_path) and r.paths[p].action == 'D':
            p2 = p[len(diff_path)+1:].strip('/')
            if p2 in parentctx:
                files_data[p2] = None
                continue
            # If this isn't in the parent ctx, it must've been a dir
            files_data.update([(f, None) for f in parentctx if f.startswith(p2 + '/')])

    for f in files_data:
        touched_files[f] = 1

    copies = getcopies(svn, hg_editor, branch, diff_path, r, touched_files,
                       parentctx)

    def filectxfn(repo, memctx, path):
        if path in files_data and files_data[path] is None:
            raise IOError()

        if path in binary_files:
            data, mode = svn.get_file(diff_path + '/' + path, r.revnum)
            isexe = 'x' in mode
            islink = 'l' in mode
        else:
            isexe = exec_files.get(path, 'x' in parentctx.flags(path))
            islink = link_files.get(path, 'l' in parentctx.flags(path))
            data = ''
            if path in files_data:
                data = files_data[path]
                if islink:
                    data = data[len('link '):]
            elif path in parentctx:
                data = parentctx[path].data()

        copied = copies.get(path)
        return context.memfilectx(path=path, data=data, islink=islink,
                                  isexec=isexe, copied=copied)

    return list(touched_files), filectxfn

def makecopyfinder(r, branchpath, rootdir):
    """Return a function detecting copies.

    Returned copyfinder(path) returns None if no copy information can
    be found or ((source, sourcerev), sourcepath) where "sourcepath" is the
    copy source path, "sourcerev" the source svn revision and "source" is the
    copy record path causing the copy to occur. If a single file was copied
    "sourcepath" and "source" are the same, while file copies dectected from
    directory copies return the copied source directory in "source".
    """
    # filter copy information for current branch
    branchpath = branchpath + '/'
    fullbranchpath = rootdir + branchpath
    copies = []
    for path, e in r.paths.iteritems():
        if not e.copyfrom_path:
            continue
        if not path.startswith(branchpath):
            continue
        if not e.copyfrom_path.startswith(fullbranchpath):
            # ignore cross branch copies
            continue
        dest = path[len(branchpath):]
        source = e.copyfrom_path[len(fullbranchpath):]
        copies.append((dest, (source, e.copyfrom_rev)))

    copies.sort(reverse=True)
    exactcopies = dict(copies)

    def finder(path):
        if path in exactcopies:
            return exactcopies[path], exactcopies[path][0]
        # look for parent directory copy, longest first
        for dest, (source, sourcerev) in copies:
            dest = dest + '/'
            if not path.startswith(dest):
                continue
            sourcepath = source + '/' + path[len(dest):]
            return (source, sourcerev), sourcepath
        return None

    return finder

def getcopies(svn, hg_editor, branch, branchpath, r, files, parentctx):
    """Return a mapping {dest: source} for every file copied into r.
    """
    if parentctx.node() == revlog.nullid:
        return {}

    # Extract svn copy information, group them by copy source.
    # The idea is to duplicate the replay behaviour where copies are
    # evaluated per copy event (one event for all files in a directory copy,
    # one event for single file copy). We assume that copy events match
    # copy sources in revision info.
    svncopies = {}
    finder = makecopyfinder(r, branchpath, svn.subdir)
    for f in files:
        copy = finder(f)
        if copy:
            svncopies.setdefault(copy[0], []).append((f, copy[1]))
    if not svncopies:
        return {}

    # cache changeset contexts and map them to source svn revisions
    ctxs = {}
    def getctx(svnrev):
        if svnrev in ctxs:
            return ctxs[svnrev]
        changeid = hg_editor.get_parent_revision(svnrev + 1, branch)
        ctx = None
        if changeid != revlog.nullid:
            ctx = hg_editor.repo.changectx(changeid)
        ctxs[svnrev] = ctx
        return ctx

    # check svn copies really make sense in mercurial
    hgcopies = {}
    for (sourcepath, rev), copies in svncopies.iteritems():
        sourcectx = getctx(rev)
        if sourcectx is None:
            continue
        sources = [s[1] for s in copies]
        if not hg_editor.aresamefiles(sourcectx, parentctx, sources):
            continue
        hgcopies.update(copies)
    return hgcopies

def fetch_externals(svn, branchpath, r, parentctx):
    """Extract svn:externals for the current revision and branch

    Return an externalsfile instance or None if there are no externals
    to convert and never were.
    """
    externals = svnexternals.externalsfile()
    if '.hgsvnexternals' in parentctx:
        externals.read(parentctx['.hgsvnexternals'].data())
    # Detect property additions only, changes are handled by checking
    # existing entries individually. Projects are unlikely to store
    # externals on many different root directories, so we trade code
    # duplication and complexity for a constant lookup price at every
    # revision in the common case.
    dirs = set(externals)
    if parentctx.node() == revlog.nullid:
        dirs.update([p for p,k in svn.list_files(branchpath, r.revnum) if k == 'd'])
        dirs.add('')
    else:
        branchprefix = branchpath + '/'
        for path, e in r.paths.iteritems():
            if e.action == 'D':
                continue
            if not path.startswith(branchprefix) and path != branchpath:
                continue
            kind = svn.checkpath(path, r.revnum)
            if kind != 'd':
                continue
            path = path[len(branchprefix):]
            dirs.add(path)
            if e.action == 'M' or (e.action == 'A' and e.copyfrom_path):
                # Do not recurse in copied directories, changes are marked
                # as 'M', except for the copied one.
                continue
            for child, k in svn.list_files(branchprefix + path, r.revnum):
                if k == 'd':
                    dirs.add((path + '/' + child).strip('/'))

    # Retrieve new or updated values
    for dir in dirs:
        try:
            values = svn.list_props(branchpath + '/' + dir, r.revnum)
            externals[dir] = values.get('svn:externals', '')
        except IOError:
            externals[dir] = ''

    if not externals and '.hgsvnexternals' not in parentctx:
        # Do not create empty externals files
        return None
    return externals


def fetch_branchrev(svn, hg_editor, branch, branchpath, r, parentctx):
    """Extract all 'branch' content at a given revision.

    Return a tuple (files, filectxfn) where 'files' is the list of all files
    in the branch at the given revision, and 'filectxfn' is a memctx compatible
    callable to retrieve individual file information.
    """
    files = []
    if parentctx.node() == revlog.nullid:
        # Initial revision, fetch all files
        for path, kind in svn.list_files(branchpath, r.revnum):
            if kind == 'f':
                files.append(path)
    else:
        branchprefix = branchpath + '/'
        for path, e in r.paths.iteritems():
            if not path.startswith(branchprefix):
                continue
            if not hg_editor._is_path_valid(path):
                continue
            kind = svn.checkpath(path, r.revnum)
            path = path[len(branchprefix):]
            if kind == 'f':
                files.append(path)
            elif kind == 'd':
                if e.action == 'M':
                    continue
                dirpath = branchprefix + path
                for child, k in svn.list_files(dirpath, r.revnum):
                    if k == 'f':
                        files.append(path + '/' + child)
            else:
                if path in parentctx:
                    files.append(path)
                    continue
                # Assume it's a deleted directory
                path = path + '/'
                deleted = [f for f in parentctx if f.startswith(path)]
                files += deleted

    copies = getcopies(svn, hg_editor, branch, branchpath, r, files, parentctx)

    def filectxfn(repo, memctx, path):
        data, mode = svn.get_file(branchpath + '/' + path, r.revnum)
        isexec = 'x' in mode
        islink = 'l' in mode
        copied = copies.get(path)
        return context.memfilectx(path=path, data=data, islink=islink,
                                  isexec=isexec, copied=copied)

    return files, filectxfn

def convert_rev(ui, hg_editor, svn, r):
    # this server fails at replay
    branches = hg_editor.branches_in_paths(r.paths, r.revnum, svn.checkpath, svn.list_files)
    deleted_branches = {}
    brpaths = branches.values()
    bad_branch_paths = {}
    for br, bp in branches.iteritems():
        bad_branch_paths[br] = []

        # This next block might be needed, but for now I'm omitting it until it can be
        # proven necessary.
        # for bad in brpaths:
        #     if bad.startswith(bp) and len(bad) > len(bp):
        #         bad_branch_paths[br].append(bad[len(bp)+1:])

        # We've go a branch that contains other branches. We have to be careful to
        # get results similar to real replay in this case.
        for existingbr in hg_editor.branches:
            bad = hg_editor._remotename(existingbr)
            if bad.startswith(bp) and len(bad) > len(bp):
                bad_branch_paths[br].append(bad[len(bp)+1:])
    for p in r.paths:
        if hg_editor._is_path_tag(p):
            continue
        branch = hg_editor._localname(p)
        if r.paths[p].action == 'R' and branch in hg_editor.branches:
            branchedits = sorted(filter(lambda x: x[0][1] == branch and x[0][0] < r.revnum,
                                        hg_editor.revmap.iteritems()), reverse=True)
            is_closed = False
            if len(branchedits) > 0:
                branchtip = branchedits[0][1]
                for child in hg_editor.repo[branchtip].children():
                    if child.branch() == 'closed-branches':
                        is_closed = True
                        break
                if not is_closed:
                    deleted_branches[branch] = branchtip

    date = hg_editor.fixdate(r.date)
    check_deleted_branches = set()
    for b in branches:
        parentctx = hg_editor.repo[hg_editor.get_parent_revision(r.revnum, b)]
        if parentctx.branch() != (b or 'default'):
            check_deleted_branches.add(b)
        kind = svn.checkpath(branches[b], r.revnum)
        if kind != 'd':
            # Branch does not exist at this revision. Get parent revision and
            # remove everything.
            deleted_branches[b] = parentctx.node()
            continue
        else:
            try:
                files_touched, filectxfn2 = diff_branchrev(
                    ui, svn, hg_editor, b, r, parentctx)
            except BadPatchApply, e:
                # Either this revision or the previous one does not exist.
                ui.status("Fetching entire revision: %s.\n" % e.args[0])
                files_touched, filectxfn2 = fetch_branchrev(
                    svn, hg_editor, b, branches[b], r, parentctx)

            externals = fetch_externals(svn, branches[b], r, parentctx)
            if externals is not None:
                files_touched.append('.hgsvnexternals')

            def filectxfn(repo, memctx, path):
                if path == '.hgsvnexternals':
                    if not externals:
                        raise IOError()
                    return context.memfilectx(path=path, data=externals.write(),
                                              islink=False, isexec=False, copied=None)
                for bad in bad_branch_paths[b]:
                    if path.startswith(bad):
                        raise IOError()
                return filectxfn2(repo, memctx, path)

        extra = util.build_extra(r.revnum, b, svn.uuid, svn.subdir)
        if '' in files_touched:
            files_touched.remove('')
        excluded = [f for f in files_touched
                    if not hg_editor._is_file_included(f)]
        for f in excluded:
            files_touched.remove(f)
        if parentctx.node() != node.nullid or files_touched:
            for f in files_touched:
                if f:
                    # this is a case that really shouldn't ever happen, it means something
                    # is very wrong
                    assert f[0] != '/'
            current_ctx = context.memctx(hg_editor.repo,
                                         [parentctx.node(), revlog.nullid],
                                         r.message or util.default_commit_msg,
                                         files_touched,
                                         filectxfn,
                                         hg_editor.authors[r.author],
                                         date,
                                         extra)
            branch = extra.get('branch', None)
            if not hg_editor.usebranchnames:
                extra.pop('branch', None)
            ha = hg_editor.repo.commitctx(current_ctx)
            if not branch in hg_editor.branches:
                hg_editor.branches[branch] = None, 0, r.revnum
            hg_editor.add_to_revmap(r.revnum, b, ha)
            hg_editor._save_metadata()
            util.describe_commit(ui, ha, b)
    # These are branches which would have an 'R' status in svn log. This means they were
    # replaced by some other branch, so we need to verify they get marked as closed.
    for branch in check_deleted_branches:
        branchedits = sorted(filter(lambda x: x[0][1] == branch and x[0][0] < r.revnum,
                                    hg_editor.revmap.iteritems()), reverse=True)
        is_closed = False
        if len(branchedits) > 0:
            branchtip = branchedits[0][1]
            for child in hg_editor.repo[branchtip].children():
                if child.branch() == 'closed-branches':
                    is_closed = True
                    break
            if not is_closed:
                deleted_branches[branch] = branchtip
    for b, parent in deleted_branches.iteritems():
        if parent == node.nullid:
            continue
        parentctx = hg_editor.repo[parent]
        files_touched = parentctx.manifest().keys()
        def filectxfn(repo, memctx, path):
            raise IOError()
        closed = node.nullid
        if 'closed-branches' in hg_editor.repo.branchtags():
            closed = hg_editor.repo['closed-branches'].node()
        parents = (parent, closed)
        extra = {}
        if hg_editor.usebranchnames:
                extra['branch'] = 'closed-branches'
        current_ctx = context.memctx(hg_editor.repo,
                                     parents,
                                     r.message or util.default_commit_msg,
                                     files_touched,
                                     filectxfn,
                                     hg_editor.authors[r.author],
                                     date,
                                     extra)
        ha = hg_editor.repo.commitctx(current_ctx)
        ui.status('Marked branch %s as closed.\n' % (b or 'default'))
        hg_editor._save_metadata()
