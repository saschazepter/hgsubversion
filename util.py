import os
import shutil

from mercurial import hg
from mercurial import node
from mercurial import util as hgutil


def getuserpass(opts):
    # DO NOT default the user to hg's getuser(). If you provide
    # *any* default username to Subversion, it won't use any remembered
    # username for the desired realm, breaking OS X Keychain support,
    # GNOME keyring support, and all similar tools.
    return opts.get('username', None), opts.get('password', '')


def version(ui):
    """Guess the version of hgsubversion.
    """
    # TODO make this say something other than "unknown" for installed hgsubversion
    repo = hg.repository(ui, os.path.dirname(__file__))
    ver = repo.dirstate.parents()[0]
    return node.hex(ver)[:12]


def normalize_url(svnurl):
    if svnurl.startswith('svn+http'):
        svnurl = svnurl[4:]
    return svnurl.rstrip('/')


REVMAP_FILE_VERSION = 1
def parse_revmap(revmap_filename):
    revmap = {}
    f = open(revmap_filename)
    ver = int(f.readline())
    if ver == 1:
        for l in f:
            revnum, node_hash, branch = l.split(' ', 2)
            if branch == '\n':
                branch = None
            else:
                branch = branch[:-1]
            revmap[int(revnum), branch] = node.bin(node_hash)
        f.close()
    else: #pragma: no cover
        print ('Your revmap was made by a newer version of hgsubversion.'
               ' Please upgrade.')
        raise NotImplementedError
    return revmap


class PrefixMatch(object):
    def __init__(self, prefix):
        self.p = prefix

    def files(self):
        return []

    def __call__(self, fn):
        return fn.startswith(self.p)

def outgoing_revisions(ui, repo, hg_editor, reverse_map, sourcerev):
    """Given a repo and an hg_editor, determines outgoing revisions for the
    current working copy state.
    """
    outgoing_rev_hashes = []
    if sourcerev in reverse_map:
        return
    sourcerev = repo[sourcerev]
    while (not sourcerev.node() in reverse_map
           and sourcerev.node() != node.nullid):
        outgoing_rev_hashes.append(sourcerev.node())
        sourcerev = sourcerev.parents()
        if len(sourcerev) != 1:
            raise hgutil.Abort("Sorry, can't find svn parent of a merge revision.")
        sourcerev = sourcerev[0]
    if sourcerev.node() != node.nullid:
        return outgoing_rev_hashes

def build_extra(revnum, branch, uuid, subdir):
    extra = {}
    branchpath = 'trunk'
    if branch:
        extra['branch'] = branch
        branchpath = 'branches/%s' % branch
    if subdir and subdir[-1] == '/':
        subdir = subdir[:-1]
    if subdir and subdir[0] != '/':
        subdir = '/' + subdir
    extra['convert_revision'] = 'svn:%(uuid)s%(path)s@%(rev)s' % {
        'uuid': uuid,
        'path': '%s/%s' % (subdir , branchpath),
        'rev': revnum,
        }
    return extra


def is_svn_repo(repo):
    return os.path.exists(os.path.join(repo.path, 'svn'))

default_commit_msg = '*** empty log message ***'

def describe_revision(ui, r):
    try:
        msg = [s for s in map(str.strip, r.message.splitlines()) if s][0]
    except:
        msg = default_commit_msg

    ui.status(('[r%d] %s: %s' % (r.revnum, r.author, msg))[:80] + '\n')

def describe_commit(ui, h, b):
    ui.note(' committed to "%s" as %s\n' % ((b or 'default'), node.short(h)))


def swap_out_encoding(new_encoding="UTF-8"):
    """ Utility for mercurial incompatibility changes, can be removed after 1.3"""
    try:
        from mercurial import encoding
        old = encoding.encoding
        encoding.encoding = new_encoding
    except ImportError:
        old = hgutil._encoding
        hgutil._encoding = new_encoding
    return old
