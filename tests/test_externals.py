import cStringIO
import os
import shutil
import sys
import tempfile
import unittest

from mercurial import hg
from mercurial import ui
from mercurial import node

import fetch_command
import svnexternals
import test_util


class TestFetchExternals(test_util.TestBase):
    def test_externalsfile(self):
        f = svnexternals.externalsfile()
        f['t1'] = 'dir1 -r10 svn://foobar'
        f['t 2'] = 'dir2 -r10 svn://foobar'
        f['t3'] = ['dir31 -r10 svn://foobar', 'dir32 -r10 svn://foobar']
        
        refext = """\
[t 2]
 dir2 -r10 svn://foobar
[t1]
 dir1 -r10 svn://foobar
[t3]
 dir31 -r10 svn://foobar
 dir32 -r10 svn://foobar
"""
        value = f.write()
        self.assertEqual(refext, value)

        f2 = svnexternals.externalsfile()
        f2.read(value)
        self.assertEqual(sorted(f), sorted(f2))
        for t in f:
            self.assertEqual(f[t], f2[t])

    def test_externals(self, stupid=False):
        repo = self._load_fixture_and_fetch('externals.svndump', stupid=stupid)

        ref0 = """\
[.]
 ../externals/project1 deps/project1
"""
        self.assertEqual(ref0, repo[0]['.hgsvnexternals'].data())
        ref1 = """\
[.]
 ../externals/project1 deps/project1
 ../externals/project2 deps/project2
"""
        self.assertEqual(ref1, repo[1]['.hgsvnexternals'].data())

        ref2 = """\
[.]
 ../externals/project2 deps/project2
[subdir]
 ../externals/project1 deps/project1
[subdir2]
 ../externals/project1 deps/project1
"""
        self.assertEqual(ref2, repo[2]['.hgsvnexternals'].data())

        ref3 = """\
[.]
 ../externals/project2 deps/project2
[subdir2]
 ../externals/project1 deps/project1
"""
        self.assertEqual(ref3, repo[3]['.hgsvnexternals'].data())

        ref4 = """\
[.]
 ../externals/project2 deps/project2
"""
        self.assertEqual(ref4, repo[4]['.hgsvnexternals'].data())

    def test_externals_stupid(self):
        self.test_externals(True)

def suite():
    all = [unittest.TestLoader().loadTestsFromTestCase(TestFetchExternals),
          ]
    return unittest.TestSuite(all)
