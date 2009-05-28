import os
import sys
import unittest

import test_util
import test_binaryfiles
import test_diff
import test_externals
import test_fetch_branches
import test_fetch_command
import test_fetch_command_regexes
import test_fetch_exec
import test_fetch_mappings
import test_fetch_renames
import test_fetch_symlinks
import test_fetch_truncated
import test_pull
import test_push_command
import test_push_renames
import test_push_dirs
import test_push_eol
import test_rebuildmeta
import test_svnwrap
import test_tags
import test_utility_commands
import test_urls

if __name__ == '__main__':

    kwargs = {'descriptions': 2}
    if '-v' in sys.argv:
        kwargs['descriptions'] = 3
        kwargs['verbosity'] = 2

    # silence output when running outside nose
    sys.stdout = os.tmpfile()

    all = globals()
    all = dict((k, v) for (k, v) in all.iteritems() if k.startswith('test_'))
    del all['test_util']

    args = [i for i in sys.argv[1:] if i.startswith('test')]
    args = [i.split('.py')[0].replace('-', '_') for i in args]

    if not args:
        suite = [i[1].suite() for i in sorted(all.iteritems())]
    else:
        suite = []
        for arg in args:
            if arg not in all:
                print 'test module %s not available' % arg
            else:
                suite.append(all[arg].suite())

    runner = unittest.TextTestRunner(**kwargs)
    runner.run(unittest.TestSuite(suite))
