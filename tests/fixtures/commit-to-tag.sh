#!/bin/sh
mkdir temp
cd temp
svnadmin create repo
REPOPATH="file://`pwd`/repo"
svn co $REPOPATH wc
cd wc
mkdir -p branches/magic trunk tags
svn add *
svn ci -m 'btt'
cd branches/magic
for a in alpha beta gamma; do
    echo $a > $a
    svn add $a
    svn ci -m "Add file $a"
done
cd ../..
svn up
svn cp $REPOPATH/branches/magic $REPOPATH/tags/will-edit -m 'Make tag to edit'
svn up

cd branches/magic
for a in delta iota lambda; do
    echo $a > $a
    svn add $a
    svn ci -m "Add file $a"
done
cd ../..

cd tags/will-edit
svn rm alpha
svn ci -m 'removed alpha on a tag. Moves tag, implicit branch.'
cd ../..

cd branches/magic
for a in omega; do
    echo $a > $a
    svn add $a
    svn ci -m "Add file $a"
done
cd ../..
svn up
svn cp $REPOPATH/branches/magic $REPOPATH/tags/also-edit -m 'Make tag to edit'
svn up

echo not omega > branches/magic/omega
echo not omega > tags/also-edit/omega
svn ci -m 'edit both the tag and its source branch at the same time'

echo more stupidity > tags/also-edit/omega
svn ci -m 'Edit an edited tag.'

svn cp $REPOPATH/tags/also-edit $REPOPATH/tags/did-edits -m 'Tag an edited tag'

svn cp $REPOPATH/branches/magic $REPOPATH/branches/closeme -m 'Make extra branch for another bogus case'
svn cp $REPOPATH/branches/closeme $REPOPATH/tags/edit-later -m 'Make tag to edit after branch closes'
svn rm $REPOPATH/branches/closeme -m 'Close the branch'
svn up
echo boofar > tags/edit-later/delta
svn ci -m 'Edit this tag after its parent closed'

cd ../..
svnadmin dump temp/repo > commit-to-tag.svndump
echo
echo 'Complete.'
echo 'You probably want to clean up temp now.'
echo 'Dump in commit-to-tag.svndump'
exit 0
