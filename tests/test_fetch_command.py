import os
import shutil
import tempfile
import unittest

from mercurial import hg
from mercurial import ui
from mercurial import node

import fetch_command
import test_util


class TestBasicRepoLayout(unittest.TestCase):
    def setUp(self):
        self.oldwd = os.getcwd()
        self.tmpdir = tempfile.mkdtemp('svnwrap_test')
        self.repo_path = '%s/testrepo' % self.tmpdir
        self.wc_path = '%s/testrepo_wc' % self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir)
        os.chdir(self.oldwd)

    def _load_fixture_and_fetch(self, fixture_name):
        return test_util.load_fixture_and_fetch(fixture_name, self.repo_path,
                                                self.wc_path)

    def test_fresh_fetch_single_rev(self):
        repo = self._load_fixture_and_fetch('single_rev.svndump')
        self.assertEqual(node.hex(repo['tip'].node()),
                         'a47d0ce778660a91c31bf2c21c448e9ee296ac90')
        self.assertEqual(repo['tip'], repo[0])

    def test_fresh_fetch_two_revs(self):
        repo = self._load_fixture_and_fetch('two_revs.svndump')
        # TODO there must be a better way than repo[0] for this check
        self.assertEqual(node.hex(repo[0].node()),
                         'a47d0ce778660a91c31bf2c21c448e9ee296ac90')
        self.assertEqual(node.hex(repo['tip'].node()),
                         'bf3767835b3b32ecc775a298c2fa27134dd91c11')
        self.assertEqual(repo['tip'], repo[1])

    def test_branches(self):
        repo = self._load_fixture_and_fetch('simple_branch.svndump')
        # TODO there must be a better way than repo[0] for this check
        self.assertEqual(node.hex(repo[0].node()),
                         'a47d0ce778660a91c31bf2c21c448e9ee296ac90')
        self.assertEqual(node.hex(repo['tip'].node()),
                         '9dfb0a19494f45c36e22f3c6d1b21d80638a7f6e')
        self.assertEqual(len(repo['tip'].parents()), 1)
        self.assertEqual(repo['tip'].parents()[0], repo['default'])
        self.assertEqual(len(repo.heads()), 1)

    def test_two_branches_with_heads(self):
        repo = self._load_fixture_and_fetch('two_heads.svndump')
        # TODO there must be a better way than repo[0] for this check
        self.assertEqual(node.hex(repo[0].node()),
                         'a47d0ce778660a91c31bf2c21c448e9ee296ac90')
        self.assertEqual(node.hex(repo['tip'].node()),
                         'a595c77cfcaa3d1ba9e04b2c55c68bc6bf2b0fbf')
        self.assertEqual(node.hex(repo['the_branch'].node()),
                         '8ccaba5f0eae124487e413abd904a013f7f6fdeb')
        self.assertEqual(node.hex(repo['the_branch'].parents()[0].node()),
                         '9dfb0a19494f45c36e22f3c6d1b21d80638a7f6e')
        self.assertEqual(len(repo['tip'].parents()), 1)
        self.assertEqual(repo['tip'], repo['default'])
        self.assertEqual(len(repo.heads()), 2)

    def test_many_special_cases_replay(self):
        repo = self._load_fixture_and_fetch('many_special_cases.svndump')
        # TODO there must be a better way than repo[0] for this check
        self._many_special_cases_checks(repo)


    def test_many_special_cases_diff(self):
        repo = self._load_fixture_and_fetch('many_special_cases.svndump')
        # TODO there must be a better way than repo[0] for this check
        self._many_special_cases_checks(repo)

    def _many_special_cases_checks(self, repo):
        self.assertEqual(node.hex(repo[0].node()),
                         'a47d0ce778660a91c31bf2c21c448e9ee296ac90')
        self.assertEqual(node.hex(repo['tip'].node()),
                         'a7742757189db8aea5f4c6721cbbfb1a09f00ddf')
        self.assertEqual(node.hex(repo['the_branch'].node()),
                         '8ccaba5f0eae124487e413abd904a013f7f6fdeb')
        self.assertEqual(node.hex(repo['the_branch'].parents()[0].node()),
                         '9dfb0a19494f45c36e22f3c6d1b21d80638a7f6e')
        self.assertEqual(len(repo['tip'].parents()), 1)
        self.assertEqual(repo['tip'], repo['default'])
        self.assertEqual(len(repo.heads()), 2)

    def test_file_mixed_with_branches(self):
        repo = self._load_fixture_and_fetch('file_mixed_with_branches.svndump')
        self.assertEqual(node.hex(repo['tip'].node()),
                         'a47d0ce778660a91c31bf2c21c448e9ee296ac90')
        assert 'README' not in repo


class TestStupidPull(unittest.TestCase):
    def setUp(self):
        self.oldwd = os.getcwd()
        self.tmpdir = tempfile.mkdtemp('svnwrap_test')
        self.repo_path = '%s/testrepo' % self.tmpdir
        self.wc_path = '%s/testrepo_wc' % self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir)
        os.chdir(self.oldwd)

    def test_stupid(self):
        test_util.load_svndump_fixture(self.repo_path, 'two_heads.svndump')
        fetch_command.fetch_revisions(ui.ui(),
                                      svn_url='file://%s' % self.repo_path,
                                      hg_repo_path=self.wc_path,
                                      stupid=True)
        repo = hg.repository(ui.ui(), self.wc_path)
        # TODO there must be a better way than repo[0] for this check
        self.assertEqual(node.hex(repo[0].node()),
                         'a47d0ce778660a91c31bf2c21c448e9ee296ac90')
        self.assertEqual(node.hex(repo['tip'].node()),
                         'a595c77cfcaa3d1ba9e04b2c55c68bc6bf2b0fbf')
        self.assertEqual(node.hex(repo['the_branch'].node()),
                         '8ccaba5f0eae124487e413abd904a013f7f6fdeb')
        self.assertEqual(node.hex(repo['the_branch'].parents()[0].node()),
                         '9dfb0a19494f45c36e22f3c6d1b21d80638a7f6e')
        self.assertEqual(len(repo['tip'].parents()), 1)
        self.assertEqual(repo['tip'], repo['default'])
        self.assertEqual(len(repo.heads()), 2)

def suite():
    all = [unittest.TestLoader().loadTestsFromTestCase(TestBasicRepoLayout),
           unittest.TestLoader().loadTestsFromTestCase(TestStupidPull),
          ]
    return unittest.TestSuite(all)
