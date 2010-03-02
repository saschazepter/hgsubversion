#!/bin/sh

RSVN="`pwd`/rsvn.py"
export PATH=/bin:/usr/bin
mkdir temp
cd temp

svnadmin create repo
svn co file://`pwd`/repo wc

cd wc
mkdir trunk branches
cd trunk
echo a > a
cd ..
svn add *
svn ci -m 'initial'

svn up
svn cp trunk branches/branch1
svn ci -m 'branch1'
svn up
echo b > branches/branch1/b
echo d > branches/branch1/d
svn add branches/branch1/b branches/branch1/d
svn ci -m 'add b to branch1'
svn cp trunk branches/branch2
svn ci -m 'branch2'
svn up
echo c > branches/branch2/c
svn add branches/branch2/c
svn ci -m 'add c to branch2'
svn up

# Clobber branch1 with branch2
cd ..
cat > clobber.rsvn <<EOF
rdelete branches/branch1
rcopy branches/branch2 branches/branch1
rcopy branches/branch1/d branches/branch1/a
EOF

python $RSVN --message=blah --username=evil `pwd`/repo < clobber.rsvn

svnadmin dump repo > ../replace_branch_with_branch.svndump
