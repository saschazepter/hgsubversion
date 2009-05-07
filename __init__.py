'''integration with Subversion repositories

This extension allows Mercurial to act as a Subversion client, for
fast incremental, bidirectional updates.

It is *not* ready yet for production use. You should only be using
this if you're ready to hack on it, and go diving into the internals
of Mercurial and/or Subversion.

Before using hgsubversion, it is *strongly* encouraged to run the
automated tests. See `README' in the hgsubversion directory for
details.
'''

import os
import sys
import traceback

from mercurial import commands
from mercurial import extensions
from mercurial import hg
from mercurial import util as hgutil

from svn import core

import svncommands
import tag_repo
import util
import wrappers
import svnexternals

def uisetup(ui):
    """Do our UI setup.

    Does the following wrappings:
     * parent -> utility_commands.parent
     * outgoing -> utility_commands.outgoing
     """
    entry = extensions.wrapcommand(commands.table, 'parents',
                                   wrappers.parent)
    entry[1].append(('', 'svn', None, "show parent svn revision instead"))
    entry = extensions.wrapcommand(commands.table, 'outgoing',
                                   wrappers.outgoing)
    entry[1].append(('', 'svn', None, "show revisions outgoing to subversion"))
    entry = extensions.wrapcommand(commands.table, 'diff',
                                   wrappers.diff)
    entry[1].append(('', 'svn', None,
                     "show svn-style diffs, default against svn parent"))
    entry = extensions.wrapcommand(commands.table, 'push',
                                   wrappers.push)
    entry[1].append(('', 'svn', None, "push to subversion"))
    entry[1].append(('', 'svn-stupid', None, "use stupid replay during push to svn"))

    try:
        rebase = extensions.find('rebase')
        if rebase:
            entry = extensions.wrapcommand(rebase.cmdtable, 'rebase', wrappers.rebase)
            entry[1].append(('', 'svn', None, 'automatic svn rebase', ))
    except:
        pass


def svn(ui, repo, subcommand, *args, **opts):
    '''see detailed help for list of subcommands'''

    # guess command if prefix
    if subcommand not in svncommands.table:
        candidates = []
        for c in svncommands.table:
            if c.startswith(subcommand):
                candidates.append(c)
        if len(candidates) == 1:
            subcommand = candidates[0]

    path = os.path.dirname(repo.path)
    try:
        commandfunc = svncommands.table[subcommand]
        if subcommand not in svncommands.nourl:
            opts['svn_url'] = open(os.path.join(repo.path, 'svn', 'url')).read()
        return commandfunc(ui, args=args, hg_repo_path=path, repo=repo, **opts)
    except core.SubversionException, e:
        if e.apr_err == core.SVN_ERR_RA_SERF_SSL_CERT_UNTRUSTED:
            raise hgutil.Abort('It appears svn does not trust the ssl cert for this site.\n'
                     'Please try running svn ls on that url first.')
        raise
    except TypeError:
        tb = traceback.extract_tb(sys.exc_info()[2])
        if len(tb) == 1:
            ui.status('Bad arguments for subcommand %s\n' % subcommand)
        else:
            raise
    except KeyError, e:
        tb = traceback.extract_tb(sys.exc_info()[2])
        if len(tb) == 1:
            ui.status('Unknown subcommand %s\n' % subcommand)
        else:
            raise

def reposetup(ui, repo):
    if repo.local():
       tag_repo.generate_repo_class(ui, repo)

for scheme in ('svn', 'svn+ssh', 'svn+http', 'svn+file'):
    hg.schemes[scheme] = tag_repo

cmdtable = {
    "svn":
        (svn,
         [('u', 'svn-url', '', 'path to the Subversion server.'),
          ('', 'stupid', False, 'be stupid and use diffy replay.'),
          ('A', 'authors', '', 'username mapping filename'),
          ('', 'filemap', '',
           'remap file to exclude paths or include only certain paths'),
          ('', 'force', False, 'force an operation to happen'),
          ('', 'username', '', 'username for authentication'),
          ('', 'password', '', 'password for authentication'),
          ],
         svncommands._helpgen(),
         ),
}
