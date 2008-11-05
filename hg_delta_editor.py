import cStringIO
import cPickle as pickle
import os
import sys
import tempfile
import traceback

from mercurial import context
from mercurial import hg
from mercurial import ui
from mercurial import util
from mercurial import revlog
from mercurial import node
from svn import delta
from svn import core

import util as our_util

def pickle_atomic(data, file_path, dir=None):
    """pickle some data to a path atomically.

    This is present because I kept corrupting my revmap by managing to hit ^C
    during the pickle of that file.
    """
    try:
        f, path = tempfile.mkstemp(prefix='pickling', dir=dir)
        f = os.fdopen(f, 'w')
        pickle.dump(data, f)
        f.close()
    except:
        raise
    else:
        util.rename(path, file_path)

def stash_exception_on_self(fn):
    """Stash any exception raised in the method on self.

    This is required because the SWIG bindings just mutate any exception into
    a generic Subversion exception with no way of telling what the original was.
    This allows the editor object to notice when you try and commit and really
    got an exception in the replay process.
    """
    def fun(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except:
            if not hasattr(self, '_exception_info'):
                self._exception_info = sys.exc_info()
            raise
    return fun


class HgChangeReceiver(delta.Editor):
    def add_to_revmap(self, revnum, branch, node_hash):
        f = open(self.revmap_file, 'a')
        f.write(str(revnum) + ' ' + node.hex(node_hash) + ' ' + (branch or '') + '\n')
        f.flush()
        f.close()
        self.revmap[revnum, branch] = node_hash

    def __init__(self, path, ui_=None, subdir='', author_host='',
                 tag_locations=['tags']):
        """path is the path to the target hg repo.

        subdir is the subdirectory of the edits *on the svn server*.
        It is needed for stripping paths off in certain cases.
        """
        if not ui_:
            ui_ = ui.ui()
        self.ui = ui_
        self.path = path
        self.__setup_repo(path)
        self.subdir = subdir
        if self.subdir and self.subdir[0] == '/':
            self.subdir = self.subdir[1:]
        self.revmap = {}
        if os.path.exists(self.revmap_file):
            self.revmap = our_util.parse_revmap(self.revmap_file)
        self.branches = {}
        if os.path.exists(self.branch_info_file):
            f = open(self.branch_info_file)
            self.branches = pickle.load(f)
            f.close()
        self.tags = {}
        if os.path.exists(self.tag_info_file):
            f = open(self.tag_info_file)
            self.tags = pickle.load(f)
            f.close()
        if os.path.exists(self.tag_locations_file):
            f = open(self.tag_locations_file)
            self.tag_locations = pickle.load(f)
            f.close()
        else:
            self.tag_locations = tag_locations
        pickle_atomic(self.tag_locations, self.tag_locations_file,
                      self.meta_data_dir)

        self.clear_current_info()
        self.author_host = author_host

    def __setup_repo(self, repo_path):
        '''Verify the repo is going to work out for us.

        This method will fail an assertion if the repo exists but doesn't have
        the Subversion metadata.
        '''
        if os.path.isdir(repo_path) and len(os.listdir(repo_path)):
            self.repo = hg.repository(self.ui, repo_path)
            assert os.path.isfile(self.revmap_file)
            assert os.path.isfile(self.svn_url_file)
            assert os.path.isfile(self.uuid_file)
            assert os.path.isfile(self.last_revision_handled_file)
        else:
            self.repo = hg.repository(self.ui, repo_path, create=True)
            os.makedirs(os.path.dirname(self.uuid_file))
            f = open(self.revmap_file, 'w')
            f.write('%s\n' % our_util.REVMAP_FILE_VERSION)
            f.flush()
            f.close()

    def clear_current_info(self):
        '''Clear the info relevant to a replayed revision so that the next
        revision can be replayed.
        '''
        self.current_files = {}
        self.deleted_files = {}
        self.current_rev = None
        self.current_files_exec = {}
        self.current_files_symlink = {}
        # Map fully qualified destination file paths to module source path
        self.copies = {}
        self.missing_plaintexts = set()
        self.commit_branches_empty = {}
        self.base_revision = None

    def _save_metadata(self):
        '''Save the Subversion metadata. This should really be called after
        every revision is created.
        '''
        pickle_atomic(self.branches, self.branch_info_file, self.meta_data_dir)
        pickle_atomic(self.tags, self.tag_info_file, self.meta_data_dir)

    def branches_in_paths(self, paths):
        '''Given a list of paths, return the set of branches that are touched.
        '''
        branches = set([])
        for p in paths:
            if self._is_path_valid(p):
                junk, branch = self._path_and_branch_for_path(p)
                branches.add(branch)
        return branches

    def _path_and_branch_for_path(self, path):
        '''Figure out which branch inside our repo this path represents, and
        also figure out which path inside that branch it is.

        Raises an exception if it can't perform its job.
        '''
        path = self._normalize_path(path)
        if path.startswith('trunk'):
            p = path[len('trunk'):]
            if p and p[0] == '/':
                p = p[1:]
            return p, None
        elif path.startswith('branches/'):
            p = path[len('branches/'):]
            br = p.split('/')[0]
            p = p[len(br)+1:]
            if p and p[0] == '/':
                p = p[1:]
            return p, br
        return None, None
        raise Exception,'Things went boom: ' + path

    def set_current_rev(self, rev):
        '''Set the revision we're currently converting.
        '''
        self.current_rev = rev

    def _normalize_path(self, path):
        '''Normalize a path to strip of leading slashes and our subdir if we
        have one.
        '''
        if path and path[0] == '/':
            path = path[1:]
        if path and path.startswith(self.subdir):
            path = path[len(self.subdir):]
        if path and path[0] == '/':
            path = path[1:]
        return path

    def _is_path_valid(self, path):
        path = self._normalize_path(path)
        if path.startswith('trunk'):
            return True
        elif path.startswith('branches/'):
            br = path.split('/')[1]
            return len(br) > 0
        return False

    def _is_path_tag(self, path):
        """If path represents the path to a tag, returns the tag name.

        Otherwise, returns False.
        """
        path = self._normalize_path(path)
        for tags_path in self.tag_locations:
            if path and (path.startswith(tags_path) and
                         len(path) > len('%s/' % tags_path)):
                return path[len(tags_path)+1:].split('/')[0]
        return False

    def get_parent_svn_branch_and_rev(self, number, branch):
        number -= 1
        if (number, branch) in self.revmap:
            return number, branch
        real_num = 0
        for num, br in self.revmap.iterkeys():
            if br != branch:
                continue
            if num <= number and num > real_num:
                real_num = num
        if real_num == 0:
            if branch in self.branches:
                parent_branch = self.branches[branch][0]
                parent_branch_rev = self.branches[branch][1]
                branch_created_rev = self.branches[branch][2]
                if parent_branch == 'trunk':
                    parent_branch = None
                if branch_created_rev <= number+1 and branch != parent_branch:
                    return self.get_parent_svn_branch_and_rev(
                                                    parent_branch_rev+1,
                                                    parent_branch)
        if real_num != 0:
            return real_num, branch
        return None, None

    def get_parent_revision(self, number, branch):
        '''Get the parent revision hash for a commit on a specific branch.
        '''
        r, br = self.get_parent_svn_branch_and_rev(number, branch)
        if r is not None:
            return self.revmap[r, br]
        return revlog.nullid

    def update_branch_tag_map_for_rev(self, revision):
        paths = revision.paths
        added_branches = {}
        added_tags = {}
        tags_to_delete = set()
        branches_to_delete = set()
        for p in paths:
            if self._is_path_valid(p):
                fi, br = self._path_and_branch_for_path(p)
                if fi == '' and br not in self.branches:
                    src_p = paths[p].copyfrom_path
                    src_rev = paths[p].copyfrom_rev
                    src_tag = self._is_path_tag(src_p)

                    if not ((src_p and self._is_path_valid(src_p)) or
                            (src_tag and src_tag in self.tags)):
                        # we'll imply you're a branch off of trunk
                        # if you have no path, but if you do, it must be valid
                        # or else we assume trunk as well
                        src_branch = None
                        src_rev = revision.revnum
                    elif src_tag:
                        # this is a branch created from a tag. Note that this
                        # really does happen (see Django)
                        src_branch, src_rev = self.tags[src_tag]
                        added_branches[br] = (src_branch, src_rev,
                                              revision.revnum)
                    else:
                        # Not from a tag, and from a valid repo path
                        (src_p,
                        src_branch) = self._path_and_branch_for_path(src_p)
                    added_branches[br] = src_branch, src_rev, revision.revnum
                elif fi == '' and br in self.branches:
                    br2 = br or 'default'
                    if br2 not in self.repo.branchtags() and paths[p].action == 'D':
                        branches_to_delete.add(br)
                elif br in added_branches:
                    if paths[p].copyfrom_rev > added_branches[br][1]:
                        x,y,z = added_branches[br]
                        added_branches[br] = x, paths[p].copyfrom_rev, z
            else:
                t_name = self._is_path_tag(p)
                if t_name == False:
                    continue
                src_p, src_rev = paths[p].copyfrom_path, paths[p].copyfrom_rev
                # if you commit to a tag, I'm calling you stupid and ignoring
                # you.
                if src_p is not None and src_rev is not None:
                    if self._is_path_valid(src_p):
                        file, branch = self._path_and_branch_for_path(src_p)
                    else:
                        # some crazy people make tags from other tags
                        file = ''
                        from_tag = self._is_path_tag(src_p)
                        if not from_tag:
                            continue
                        branch, src_rev = self.tags[from_tag]
                    if t_name not in added_tags:
                        added_tags[t_name] = branch, src_rev
                    elif file and src_rev > added_tags[t_name][1]:
                        added_tags[t_name] = branch, src_rev
                elif (paths[p].action == 'D' and p.endswith(t_name)
                      and t_name in self.tags):
                        tags_to_delete.add(t_name)
        for t in tags_to_delete:
            del self.tags[t]
        for br in branches_to_delete:
            del self.branches[br]
        self.tags.update(added_tags)
        self.branches.update(added_branches)
        self._save_metadata()

    def commit_current_delta(self):
        if hasattr(self, '_exception_info'):
            traceback.print_exception(*self._exception_info)
            raise ReplayException()
        if self.missing_plaintexts:
            raise MissingPlainTextError()
        files_to_commit = self.current_files.keys()
        files_to_commit.extend(self.current_files_symlink.keys())
        files_to_commit.extend(self.current_files_exec.keys())
        files_to_commit = sorted(list(set(files_to_commit)))
        branch_batches = {}
        rev = self.current_rev
        date = rev.date.replace('T', ' ').replace('Z', '').split('.')[0]
        date += ' -0000'

        # build up the branches that have files on them
        for f in files_to_commit:
            if not  self._is_path_valid(f):
                continue
            p, b = self._path_and_branch_for_path(f)
            if b not in branch_batches:
                branch_batches[b] = []
            branch_batches[b].append((p, f))

        for branch, files in branch_batches.iteritems():
            if branch in self.commit_branches_empty and files:
                del self.commit_branches_empty[branch]
            extra = {}
            files = dict(files)

            parents = (self.get_parent_revision(rev.revnum, branch),
                       revlog.nullid)
            if branch is not None:
                if branch not in self.branches and branch not in self.repo.branchtags():
                    continue
                extra['branch'] = branch
            parent_ctx = self.repo.changectx(parents[0])
            def filectxfn(repo, memctx, path):
                current_file = files[path]
                if current_file in self.deleted_files:
                    raise IOError()
                copied = self.copies.get(current_file)
                flags = parent_ctx.flags(path)
                is_exec = self.current_files_exec.get(current_file,
                                                      'x' in flags)
                is_link = self.current_files_symlink.get(current_file,
                                                         'l' in flags)
                if current_file in self.current_files:
                    data = self.current_files[current_file]
                    if is_link:
                        assert data.startswith('link ')
                        data = data[len('link '):]
                else:
                    data = parent_ctx.filectx(path).data()
                return context.memfilectx(path=path,
                                          data=data,
                                          islink=is_link, isexec=is_exec,
                                          copied=copied)
            current_ctx = context.memctx(self.repo,
                                         parents,
                                         rev.message or '...',
                                         files.keys(),
                                         filectxfn,
                                         '%s%s' %(rev.author, self.author_host),
                                         date,
                                         extra)
            new_hash = self.repo.commitctx(current_ctx)
            self.ui.status('committed as %s on branch %s\n' %
                           (node.hex(new_hash), (branch or 'default')))
            if (rev.revnum, branch) not in self.revmap:
                self.add_to_revmap(rev.revnum, branch, new_hash)
        # now we handle branches that need to be committed without any files
        for branch in self.commit_branches_empty:
            ha = self.get_parent_revision(rev.revnum, branch)
            if ha == node.nullid:
                continue
            parent_ctx = self.repo.changectx(ha)
            def del_all_files(*args):
                raise IOError
            extra = {}
            if branch:
                extra['branch'] = branch
            # True here means nuke all files
            files = []
            if self.commit_branches_empty[branch]:
                files = parent_ctx.manifest().keys()
            current_ctx = context.memctx(self.repo,
                                         (ha, node.nullid),
                                         rev.message or ' ',
                                         files,
                                         del_all_files,
                                         '%s%s' % (rev.author,
                                                   self.author_host),
                                         date,
                                         extra)
            new_hash = self.repo.commitctx(current_ctx)
            self.ui.status('committed as %s on branch %s\n' %
                           (node.hex(new_hash), (branch or 'default')))
            if (rev.revnum, branch) not in self.revmap:
                self.add_to_revmap(rev.revnum, branch, new_hash)
        self.clear_current_info()

    @property
    def meta_data_dir(self):
        return os.path.join(self.path, '.hg', 'svn')

    def meta_file_named(self, name):
        return os.path.join(self.meta_data_dir, name)

    @property
    def revmap_file(self):
        return self.meta_file_named('rev_map')

    @property
    def svn_url_file(self):
        return self.meta_file_named('url')

    @property
    def uuid_file(self):
        return self.meta_file_named('uuid')

    @property
    def last_revision_handled_file(self):
        return self.meta_file_named('last_rev')

    @property
    def branch_info_file(self):
        return self.meta_file_named('branch_info')

    @property
    def tag_info_file(self):
        return self.meta_file_named('tag_info')

    @property
    def tag_locations_file(self):
        return self.meta_file_named('tag_locations')

    @property
    def url(self):
        return open(self.svn_url_file).read()

    @stash_exception_on_self
    def delete_entry(self, path, revision_bogus, parent_baton, pool=None):
        if self._is_path_valid(path):
            br_path, branch = self._path_and_branch_for_path(path)
            ha = self.get_parent_revision(self.current_rev.revnum, branch)
            if ha == revlog.nullid:
                return
            ctx = self.repo.changectx(ha)
            if br_path not in ctx:
                br_path2 = ''
                if br_path != '':
                    br_path2 = br_path + '/'
                # assuming it is a directory
                def delete_x(x):
                    self.deleted_files[x] = True
                map(delete_x, [pat for pat in self.current_files.iterkeys()
                               if pat.startswith(path)])
                for f in ctx.walk(our_util.PrefixMatch(br_path2)):
                    f_p = '%s/%s' % (path, f[len(br_path2):])
                    if f_p not in self.current_files:
                        self.deleted_files[f_p] = True
                        self.current_files[f_p] = ''
                        self.ui.status('D %s\n' % f_p)
            self.deleted_files[path] = True
            self.current_files[path] = ''
            self.ui.status('D %s\n' % path)

    @stash_exception_on_self
    def open_file(self, path, parent_baton, base_revision, p=None):
        self.current_file = 'foobaz'
        if self._is_path_valid(path):
            self.current_file = path
            self.ui.status('M %s\n' % path)
            if base_revision != -1:
                self.base_revision = base_revision
            else:
                self.base_revision = None
            self.should_edit_most_recent_plaintext = True

    def _aresamefiles(self, parentctx, childctx, files):
        """Assuming all files exist in childctx and parentctx, return True
        if none of them was changed in-between.
        """
        if parentctx == childctx:
            return True
        if parentctx.rev() > childctx.rev():
            parentctx, childctx = childctx, parentctx
        
        def selfandancestors(selfctx):
            yield selfctx
            for ctx in selfctx.ancestors():
                yield ctx
                
        files = dict.fromkeys(files)
        for pctx in selfandancestors(childctx):
            if pctx.rev() <= parentctx.rev():
                return True
            for f in pctx.files():                
                if f in files:
                    return False
        # parentctx is not an ancestor of childctx, files are unrelated
        return False

    @stash_exception_on_self
    def add_file(self, path, parent_baton, copyfrom_path,
                 copyfrom_revision, file_pool=None):
        self.current_file = 'foobaz'
        self.base_revision = None
        if path in self.deleted_files:
            del self.deleted_files[path]
        if not self._is_path_valid(path):
            return
        fpath, branch = self._path_and_branch_for_path(path)
        if not fpath:
            return
        self.current_file = path
        self.should_edit_most_recent_plaintext = False
        if not copyfrom_path:
            self.ui.status('A %s\n' % path)
            return
        self.ui.status('A+ %s\n' % path)
        (from_file,
         from_branch) = self._path_and_branch_for_path(copyfrom_path)
        if not from_file:
            self.missing_plaintexts.add(path)
            return
        ha = self.get_parent_revision(copyfrom_revision + 1,
                                      from_branch)
        ctx = self.repo.changectx(ha)
        if from_file in ctx:
            fctx = ctx.filectx(from_file)
            cur_file = self.current_file
            self.current_files[cur_file] = fctx.data()
            self.current_files_symlink[cur_file] = 'l' in fctx.flags()
            self.current_files_exec[cur_file] = 'x' in fctx.flags()
        if from_branch == branch:
            parentid = self.get_parent_revision(self.current_rev.revnum,
                                                branch)
            if parentid != revlog.nullid:
                parentctx = self.repo.changectx(parentid)
                if self._aresamefiles(parentctx, ctx, [from_file]):
                    self.copies[path] = from_file

    @stash_exception_on_self
    def add_directory(self, path, parent_baton, copyfrom_path,
                      copyfrom_revision, dir_pool=None):
        if self._is_path_valid(path):
            junk, branch = self._path_and_branch_for_path(path)
            if not copyfrom_path and not junk:
                self.commit_branches_empty[branch] = True
            else:
                self.commit_branches_empty[branch] = False
        if not self._is_path_valid(path) or not copyfrom_path:
            return
        if copyfrom_path:
            tag = self._is_path_tag(copyfrom_path)
            if tag not in self.tags:
                tag = None
            if not self._is_path_valid(copyfrom_path) and not tag:
                self.missing_plaintexts.add('%s/' % path)
                return

        if tag:
            source_branch, source_rev = self.tags[tag]
            cp_f = ''
        else:
            source_rev = copyfrom_revision
            cp_f, source_branch = self._path_and_branch_for_path(copyfrom_path)
        new_hash = self.get_parent_revision(source_rev + 1,
                                            source_branch)
        if new_hash == node.nullid:
            self.missing_plaintexts.add('%s/' % path)
            return
        cp_f_ctx = self.repo.changectx(new_hash)
        if cp_f != '/' and cp_f != '':
            cp_f = '%s/' % cp_f
        else:
            cp_f = ''
        copies = {}
        for f in cp_f_ctx:
            if not f.startswith(cp_f):
                continue
            f2 = f[len(cp_f):]
            fctx = cp_f_ctx.filectx(f)
            fp_c = path + '/' + f2
            self.current_files[fp_c] = fctx.data()
            self.current_files_exec[fp_c] = 'x' in fctx.flags()
            self.current_files_symlink[fp_c] = 'l' in fctx.flags()
            if fp_c in self.deleted_files:
                del self.deleted_files[fp_c]
            if branch == source_branch:
                copies[fp_c] = f
        if copies:
            # Preserve the directory copy records if no file was changed between
            # the source and destination revisions, or discard it completely.
            parentid = self.get_parent_revision(self.current_rev.revnum, branch)
            if parentid != revlog.nullid:
                parentctx = self.repo.changectx(parentid)
                if self._aresamefiles(parentctx, cp_f_ctx, copies.values()):
                    self.copies.update(copies)

    @stash_exception_on_self
    def change_file_prop(self, file_baton, name, value, pool=None):
        if name == 'svn:executable':
            self.current_files_exec[self.current_file] = bool(value)
        elif name == 'svn:special':
            self.current_files_symlink[self.current_file] = bool(value)

    @stash_exception_on_self
    def open_directory(self, path, parent_baton, base_revision, dir_pool=None):
        if self._is_path_valid(path):
            p_, branch = self._path_and_branch_for_path(path)
            if p_ == '':
                self.commit_branches_empty[branch] = False

    @stash_exception_on_self
    def apply_textdelta(self, file_baton, base_checksum, pool=None):
        base = ''
        if not self._is_path_valid(self.current_file):
            return lambda x: None
        if (self.current_file in self.current_files
            and not self.should_edit_most_recent_plaintext):
            base = self.current_files[self.current_file]
        elif (base_checksum is not None or
              self.should_edit_most_recent_plaintext):
                p_, br = self._path_and_branch_for_path(self.current_file)
                par_rev = self.current_rev.revnum
                if self.base_revision:
                    par_rev = self.base_revision + 1
                ha = self.get_parent_revision(par_rev, br)
                if ha != revlog.nullid:
                    ctx = self.repo.changectx(ha)
                    if not p_ in ctx:
                        self.missing_plaintexts.add(self.current_file)
                        # short circuit exit since we can't do anything anyway
                        return lambda x: None
                    base = ctx.filectx(p_).data()
        source = cStringIO.StringIO(base)
        target = cStringIO.StringIO()
        self.stream = target

        handler, baton = delta.svn_txdelta_apply(source, target, None)
        if not callable(handler):
            # TODO(augie) Raise a real exception, don't just fail an assertion.
            assert False, 'handler not callable, bindings are broken'
        def txdelt_window(window):
            try:
                if not self._is_path_valid(self.current_file):
                    return
                handler(window, baton)
                # window being None means commit this file
                if not window:
                    self.current_files[self.current_file] = target.getvalue()
            except core.SubversionException, e:
                if e.message == 'Delta source ended unexpectedly':
                    self.missing_plaintexts.add(self.current_file)
                else:
                    self._exception_info = sys.exc_info()
                    raise
            except:
                print len(base), self.current_file
                self._exception_info = sys.exc_info()
                raise
        return txdelt_window

class MissingPlainTextError(Exception):
    """Exception raised when the repo lacks a source file required for replaying
    a txdelta.
    """

class ReplayException(Exception):
    """Exception raised when you try and commit but the replay encountered an
    exception.
    """
