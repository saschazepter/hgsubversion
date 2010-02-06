import os

from mercurial import util as hgutil

import svnwrap
import svnrepo
import util

def genignore(ui, repo, force=False, **opts):
    """generate .hgignore from svn:ignore properties.
    """
    ignpath = repo.wjoin('.hgignore')
    if not force and os.path.exists(ignpath):
        raise hgutil.Abort('not overwriting existing .hgignore, try --force?')
    svn = svnrepo.svnremoterepo(repo.ui).svn
    meta = repo.svnmeta()
    hashes = meta.revmap.hashes()
    parent = util.parentrev(ui, repo, meta, hashes)
    r, br = hashes[parent.node()]
    if meta.layout == 'single':
        branchpath = ''
    else:
        branchpath = br and ('branches/%s/' % br) or 'trunk/'
    ignorelines = ['.hgignore', 'syntax:glob']
    dirs = [''] + [d[0] for d in svn.list_files(branchpath, r)
                   if d[1] == 'd']
    for dir in dirs:
        path = '%s%s' % (branchpath, dir)
        props = svn.list_props(path, r)
        if 'svn:ignore' not in props:
            continue
        lines = props['svn:ignore'].strip().split('\n')
        ignorelines += [dir and (dir + '/' + prop) or prop for prop in lines]

    repo.wopener('.hgignore', 'w').write('\n'.join(ignorelines) + '\n')


def info(ui, repo, hg_repo_path, **opts):
    """show Subversion details similar to `svn info'
    """
    meta = repo.svnmeta()
    hashes = meta.revmap.hashes()

    if opts.get('rev'):
        parent = repo[opts['rev']]
    else:
        parent = util.parentrev(ui, repo, meta, hashes)

    pn = parent.node()
    if pn not in hashes:
        ui.status('Not a child of an svn revision.\n')
        return 0
    r, br = hashes[pn]
    subdir = parent.extra()['convert_revision'][40:].split('@')[0]
    if meta.layout == 'single':
        branchpath = ''
    elif br == None:
        branchpath = '/trunk'
    elif br.startswith('../'):
        branchpath = '/%s' % br[3:]
        subdir = subdir.replace('branches/../', '')
    else:
        branchpath = '/branches/%s' % br
    remoterepo = svnrepo.svnremoterepo(repo.ui)
    url = '%s%s' % (remoterepo.svnurl, branchpath)
    author = meta.authors.reverselookup(parent.user())
    # cleverly figure out repo root w/o actually contacting the server
    reporoot = url[:len(url)-len(subdir)]
    ui.status('''URL: %(url)s
Repository Root: %(reporoot)s
Repository UUID: %(uuid)s
Revision: %(revision)s
Node Kind: directory
Last Changed Author: %(author)s
Last Changed Rev: %(revision)s
Last Changed Date: %(date)s\n''' %
              {'reporoot': reporoot,
               'uuid': meta.uuid,
               'url': url,
               'author': author,
               'revision': r,
               # TODO I'd like to format this to the user's local TZ if possible
               'date': hgutil.datestr(parent.date(),
                                      '%Y-%m-%d %H:%M:%S %1%2 (%a, %d %b %Y)')
              })


def listauthors(ui, args, authors=None, **opts):
    """list all authors in a Subversion repository
    """
    if not len(args):
        ui.status('No repository specified.\n')
        return
    svn = svnrepo.svnremoterepo(ui, args[0]).svn
    author_set = set()
    for rev in svn.revisions():
        author_set.add(str(rev.author)) # So None becomes 'None'
    if authors:
        authorfile = open(authors, 'w')
        authorfile.write('%s=\n' % '=\n'.join(sorted(author_set)))
        authorfile.close()
    else:
        ui.status('%s\n' % '\n'.join(sorted(author_set)))


table = {
    'genignore': genignore,
    'info': info,
    'listauthors': listauthors,
}
