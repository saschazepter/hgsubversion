from hgsubversion import wrappers
        u.pushbuffer()
        wrappers.diff(lambda x,y,z: None, u, self.repo, svn=True)
        self.assertEqual(u.popbuffer(), expected_diff_output)