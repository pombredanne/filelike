
from filelike.wrappers import *
from filelike.tests import Test_ReadWriteSeek

import os
import tempfile
import unittest
from StringIO import StringIO


class Test_FileWrapper(Test_ReadWriteSeek):
    """Testcases for FileWrapper base class."""
    
    def makeFile(self,contents,mode):
        s = StringIO(contents)
        f = FileWrapper(s,mode)
        def getvalue():
            return s.getvalue()
        f.getvalue = getvalue
        return f


class Test_OpenerDecoders(unittest.TestCase):
    """Testcases for the filelike.Opener decoder functions."""
    
    def setUp(self):
        import tempfile
        handle, self.tfilename = tempfile.mkstemp()
        self.tfile = os.fdopen(handle,"w+b")

    def tearDown(self):
        os.unlink(self.tfilename)

    def test_LocalFile(self):
        """Test opening a simple local file."""
        self.tfile.write("contents")
        self.tfile.flush()
        f = filelike.open(self.tfilename,"r")
        self.assertEquals(f.name,self.tfilename)
        self.assertEquals(f.read(),"contents")
    
    def test_RemoteBzFile(self):
        """Test opening a remote BZ2 file."""
        f = filelike.open("http://www.rfk.id.au/static/test.txt.bz2")
        self.assertEquals(f.read(),"contents goes here if you please.\n\n")

