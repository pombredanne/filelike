# filelike/__init__.py
#
# Copyright (C) 2006-2009, Ryan Kelly
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.
#
"""

    filelike: a python module for creating and handling file-like objects.

This module takes care of the groundwork for implementing and manipulating
objects that provide a rich file-like interface, including reading, writing,
seeking and iteration.  It also provides a number of useful classes built on
top of this functionality.

The main class is FileLikeBase, which implements the entire file-like
interface on top of primitive _read(), _write(), _seek() and _tell() methods.
Subclasses may implement any or all of these methods to obtain the related
higher-level file behaviors.

It also provides some nifty file-handling functions:

    * open:    mirrors the standard open() function but is much cleverer;
               URLs are automatically fetched, .bz2 files are transparently
               decompressed, and so-on.

    * join:    concatenate multiple file-like objects together so that they
               act like a single file.

    * slice:   access a section of a file-like object as if it were an
               independent file.


The "wrappers" subpackage contains a collection of useful classes built on
top of this framework.  These include:
    
    * Translate:  pass file contents through an arbitrary translation
                  function (e.g. compression, encryption, ...)
                  
    * Decrypt:    on-the-fly reading and writing to an encrypted file
                  (using PEP272 cipher API)

    * UnBZip2:    on-the-fly decompression of bzip'd files
                  (like the standard library's bz2 module, but accepts
                  any file-like object)

As an example of the type of thing this module is designed to achieve, here's
how the Decrypt wrapper can be used to transparently access an encrypted
file:
    
    # Create the decryption key
    from Crypto.Cipher import DES
    cipher = DES.new('abcdefgh',DES.MODE_ECB)
    # Open the encrypted file
    from filelike.wrappers import Decrypt
    f = Decrypt(file("some_encrypted_file.bin","r"),cipher)
    
The object in 'f' now behaves as a file-like object, transparently decrypting
the file on-the-fly as it is read.


The "pipeline" subpackage contains facilities for composing these wrappers
in the form of a unix pipeline.  In the following example, 'f' will read the
first five lines of an encrypted file:
    
    from filelike.pipeline import Decrypt, Head
    f = file("some_encrypted_file.bin") > Decrypt(cipher) | Head(lines=5)


Finally, two utility functions are provided for when code expects to deal with
file-like objects:
    
    * is_filelike(obj):   checks that an object is file-like
    * to_filelike(obj):   wraps a variety of objects in a file-like interface

""" 

__ver_major__ = 0
__ver_minor__ = 3
__ver_patch__ = 1
__ver_sub__ = ""
__version__ = "%d.%d.%d%s" % (__ver_major__,__ver_minor__,
                              __ver_patch__,__ver_sub__)


import unittest
from StringIO import StringIO
import urllib2
import urlparse
import tempfile


class FileLikeBase(object):
    """Base class for implementing file-like objects.
    
    This class takes a lot of the legwork out of writing file-like objects
    with a rich interface.  It implements the higher-level file-like
    methods on top of four primitive methods: _read, _write, _seek and _tell.
    See their docstrings for precise details on how these methods behave.
    
    Subclasses then need only implement some subset of these methods for
    rich file-like interface compatability.  They may of course override
    other methods as desired.

    The class is missing the following attributes, which dont really make
    sense for anything but real files:
        
        * fileno()
        * isatty()
        * encoding
        * mode
        * name
        * newlines

    It is also missing the following methods purely because of a lack of
    code, and they may appear at some point in the future:

        * truncate()
        
    Unlike standard file objects, all read methods share the same buffer
    and so can be freely mixed (e.g. read(), readline(), next(), ...).

    This class understands and will accept the following mode strings,
    with any additional characters being ignored:

        * r    - open the file for reading only.
        * r+   - open the file for reading and writing.
        * r-   - open the file for streamed reading; do not allow seek/tell.
        * w    - open the file for writing only; create the file if
                 it doesn't exist; truncate it to zero length.
        * w+   - open the file for reading and writing; create the file
                 if it doesn't exist; truncate it to zero length.
        * w-   - open the file for streamed writing; do not allow seek/tell.
        * a    - open the file for writing only; create the file if it
                 doesn't exist; place pointer at end of file.
        * a+   - open the file for reading and writing; create the file
                 if it doesn't exist; place pointer at end of file.

    These are mostly standard except for the "-" indicator, which has
    been added for efficiency purposes in cases where seeking can be
    expensive to simulate (e.g. compressed files).  Note that any file
    opened for both reading and writing must also support seeking.
    
    """
    
    def __init__(self,bufsize=1024):
        """FileLikeBase Constructor.

        The optional argument 'bufsize' specifies the number of bytes to
        read at a time when looking for a newline character.  Setting this to
        a larger number when lines are long should improve efficiency.
        """
        # File-like attributes
        self.closed = False
        self.softspace = 0
        # Our own attributes
        self._bufsize = bufsize  # buffer size for chunked reading
        self._rbuffer = None     # data that's been read but not returned
        self._wbuffer = None     # data that's been given but not written
        self._sbuffer = None     # data between real & apparent file pos
        self._soffset = 0        # internal offset of file pointer

    def _check_mode(self,mode,mstr=None):
        """Check whether the file may be accessed in the given mode.

        'mode' must be one of "r" or "w", and this function returns False
        if the file-like object has a 'mode' attribute, and it does not
        permit access in that mode.  If there is no 'mode' attribute,
        True is returned.

        If seek support is not required, use "r-" or "w-" as the mode string.

        To check a mode string other than self.mode, pass it in as the
        second argument.
        """
        if mstr is None:
            try:
                mstr = self.mode
            except AttributeError:
                return True
        if "+" in mstr:
            return True
        if "-" in mstr and "-" not in mode:
            return False    
        if "r" in mode:
            if "r" not in mstr:
                return False    
        if "w" in mode:
            if "w" not in mstr and "a" not in mstr:
                return False
        return True
        
    def _assert_mode(self,mode,mstr=None):
        """Check whether the file may be accessed in the given mode.

        This method is equivalent to _check_assert(), but raises IOError
        instead of returning False.
        """
        if mstr is None:
            try:
                mstr = self.mode
            except AttributeError:
                return True
        if "+" in mstr:
            return True
        if "-" in mstr and "-" not in mode:
            raise IOError("File does not support seeking.")
        if "r" in mode:
            if "r" not in mstr:
                raise IOError("File not opened for reading")
        if "w" in mode:
            if "w" not in mstr and "a" not in mstr:
                raise IOError("File not opened for writing")
        return True
    
    def flush(self):
        """Flush internal write buffer, if necessary."""
        if self.closed:
            raise IOError("File has been closed")
        if self._check_mode("w-") and self._wbuffer is not None:
            buffered = ""
            if self._sbuffer:
                buffered = buffered + self._sbuffer
                self._sbuffer = None
            buffered = buffered + self._wbuffer
            self._wbuffer = None
            leftover = self._write(buffered,flushing=True)
            if leftover:
                raise IOError("Could not flush write buffer.")
    
    def close(self):
        """Flush write buffers and close the file.

        The file may not be accessed further once it is closed.
        """
        if not self.closed:
            self.flush()
            self.closed = True

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self,exc_type,exc_val,exc_tb):
        self.close()
        return False
    
    def next(self):
        """next() method complying with the iterator protocol.

        File-like objects are their own iterators, with each call to
        next() returning subsequent lines from the file.
        """
        ln = self.readline()
        if ln == "":
            raise StopIteration()
        return ln
    
    def __iter__(self):
        return self

    def seek(self,offset,whence=0):
        """Move the internal file pointer to the given location."""
        if whence > 2 or whence < 0:
            raise ValueError("Invalid value for 'whence': " + str(whence))
        if hasattr(self,"mode") and "-" in self.mode:
            raise IOError("File is not seekable.")
        # Ensure that there's nothing left in the write buffer
        if self._wbuffer:
            self.flush()
        # Adjust for any data left in the read buffer
        if whence == 1 and self._rbuffer:
            offset = offset - len(self._rbuffer)
        self._rbuffer = None
        # Adjust for any discrepancy in actual vs apparent seek position
        if whence == 1:
            if self._sbuffer:
                offset = offset + len(self._sbuffer)
            if self._soffset:
                offset = offset + self._soffset
        self._sbuffer = None
        self._soffset = 0
        # Shortcut the special case of staying put
        if offset == 0 and whence == 1:
            return
        # Try to do a whence-wise seek if it is implemented.
        sbuf = None
        try:
            sbuf = self._seek(offset,whence)
        except NotImplementedError:
            # Try to simulate using an absolute seek.
            try:
                if whence == 1:
                    offset = self._tell() + offset
                elif whence == 2:
                    if hasattr(self,"size"):
                        offset = self.size + offset
                    else:
                        for ln in self: pass
                        offset = self.tell() + offset
                else:
                    # absolute seek already failed, don't try again
                    raise NotImplementedError
                sbuf = self._seek(offset,0)
            except NotImplementedError:
                # Simulate by reseting to start
                self._seek(0,0)
                self._soffset = offset
        finally:
            self._sbuffer = sbuf

    def tell(self):
        """Determine current position of internal file pointer."""
        # Need to adjust for unread/unwritten data in buffers
        pos = self._tell()
        if self._rbuffer:
            pos = pos - len(self._rbuffer)
        if self._wbuffer:
            pos = pos + len(self._wbuffer)
        if self._sbuffer:
            pos = pos + len(self._sbuffer)
        if self._soffset:
            pos = pos + self._soffset
        return pos
    
    def read(self,size=-1):
        """Read at most 'size' bytes from the file.

        Bytes are returned as a string.  If 'size' is negative, zero or
        missing, the remainder of the file is read.  If EOF is encountered
        immediately, the empty string is returned.
        """
        if self.closed:
            raise IOError("File has been closed")
        self._assert_mode("r-")
        # If we were previously writing, ensure position is correct
        if self._wbuffer is not None:
            self.seek(0,1)
        # Discard any data that should have been seeked over
        if self._sbuffer:
            s = len(self._sbuffer)
            self._sbuffer = None
            self.read(s)
        elif self._soffset:
            s = self._soffset
            self._soffset = 0
            while s > self._bufsize:
                self.read(self._bufsize)
                s -= self._bufsize
            self.read(s)
        # Should the entire file be read?
        if size <= 0:
            if self._rbuffer:
                data = [self._rbuffer]
            else:
                data = []
            self._rbuffer = ""
            newData = self._read()
            while newData is not None:
                data.append(newData)
                newData = self._read()
            output = "".join(data)
        # Otherwise, we need to return a specific amount of data
        else:
            if self._rbuffer:
                newData = self._rbuffer
                data = [newData]
            else:
                newData = ""
                data = []
            sizeSoFar = len(newData)
            while sizeSoFar < size:
                newData = self._read(size-sizeSoFar)
                if newData is None:
                    break
                data.append(newData)
                sizeSoFar += len(newData)
            data = "".join(data)
            if sizeSoFar > size:
                # read too many bytes, store in the buffer
                self._rbuffer = data[size:]
                data = data[:size]
            else:
                self._rbuffer = ""
            output = data
        return output
        
    def readline(self,size=-1):
        """Read a line from the file, or at most <size> bytes."""
        bits = []
        indx = -1
        sizeSoFar = 0
        while indx == -1:
            nextBit = self.read(self._bufsize)
            bits.append(nextBit)
            sizeSoFar += len(nextBit)
            if nextBit == "":
                break
            if size > 0 and sizeSoFar >= size:
                break
            indx = nextBit.find("\n")
        # If not found, return whole string up to <size> length
        # Any leftovers are pushed onto front of buffer
        if indx == -1:
            data = "".join(bits)
            if size > 0 and sizeSoFar > size:
                extra = data[size:]
                data = data[:size]
                self._rbuffer = extra + self._rbuffer
            return data
        # If found, push leftovers onto front of buffer
        # Add one to preserve the newline in the return value
        indx += 1
        extra = bits[-1][indx:]
        bits[-1] = bits[-1][:indx]
        self._rbuffer = extra + self._rbuffer
        return "".join(bits)
    
    def readlines(self,sizehint=-1):
        """Return a list of all lines in the file."""
        return [ln for ln in self]
    
    def xreadlines(self):
        """Iterator over lines in the file - equivalent to iter(self)."""
        return iter(self)

    def write(self,string):
        """Write the given string to the file."""
        if self.closed:
            raise IOError("File has been closed")
        self._assert_mode("w-")
        # If we were previusly reading, ensure position is correct
        if self._rbuffer is not None:
            self.seek(0,1)
        # If we're actually behind the apparent position, we must also
        # write the data in the gap.
        if self._sbuffer:
            string = self._sbuffer + string
            self._sbuffer = None
        elif self._soffset:
            s = self._soffset
            self._soffset = 0
            string = self.read(s) + string
            self.seek(0,0)
        if self._wbuffer:
            string = self._wbuffer + string
        leftover = self._write(string)
        if leftover is None:
            self._wbuffer = ""
        else:
            self._wbuffer = leftover
    
    def writelines(self,seq):
        """Write a sequence of lines to the file."""
        for ln in seq:
            self.write(ln)
    
    def _read(self,sizehint=-1):
        """Read approximately <sizehint> bytes from the file-like object.
        
        This method is to be implemented by subclasses that wish to be
        readable.  It should read approximately <sizehint> bytes from the
        file and return them as a string.  If <sizehint> is missing or
        less than or equal to zero, try to read all the remaining contents.
        
        The method need not guarantee any particular number of bytes -
        it may return more bytes than requested, or fewer.  If needed, the
        size hint may be completely ignored.  It may even return an empty
        string if no data is yet available.
        
        Because of this, the method must return None to signify that EOF
        has been reached.  The higher-level methods will never indicate EOF
        until None has been read from _read().  Once EOF is reached, it
        should be safe to call _read() again, immediately returning None.
        """
        raise IOError("Object not readable")
    
    def _write(self,string,flushing=False):
        """Write the given string to the file-like object.
        
        This method must be implemented by subclasses wishing to be writable.
        It must attempt to write as much of the given data as possible to the
        file, but need not guarantee that it is all written.  It may return
        None to indicate that all data was written, or return as a string any
        data that could not be written.
        
        If the keyword argument 'flushing' is true, it indicates that the
        internal write buffers are being flushed, and *all* the given data
        is expected to be written to the file. If unwritten data is returned
        when 'flushing' is true, an IOError will be raised.
        """
        raise IOError("Object not writable")

    def _seek(self,offset,whence):
        """Set the file's internal position pointer, approximately.
 
        This method should set the file's position to approximately 'offset'
        bytes relative to the position specified by 'whence'.  If it is
        not possible to position the pointer exactly at the given offset,
        it should be positioned at a convenient *smaller* offset and the
        file data between the real and apparent position should be returned.

        At minimum, this method must implement the ability to seek to
        the start of the file, i.e. offset=0 and whence=0.  If more
        complex seeks are difficult to implement then it may raise
        NotImplementedError to have them simulated (inefficiently) by
        the higher-level mahinery of this class.
        """
        raise IOError("Object not seekable")

    def _tell(self):
        """Get the location of the file's internal position pointer.

        This method must be implemented by subclasses that wish to be
        seekable, and must return the position of the file's internal
        pointer.

        Due to buffering, the position seen by users of this class
        (the "apparent position") may be different to the position
        returned by this method (the "actual position").
        """
        raise IOError("Object not seekable")


class Opener(object):
    """Class allowing clever opening of files.
    
    Instances of this class are callable using inst(filename,mode),
    and are intended as a 'smart' replacement for the standard file
    constructor and open command.  Given a filename and a mode, it returns
    a file-like object representing that file, according to rules such
    as:
        
        * URLs are opened using urllib2
        * files with names ending in ".gz" are gunzipped on the fly
        * etc...
        
    The precise rules that are implemented are determined by two lists
    of functions - openers and decoders.  First, each successive opener
    function is called with the filename and mode until one returns non-None.
    Theese functions must attempt to open the given filename and return it as
    a filelike object.

    Once the file has been opened, it is passed to each successive decoder
    function.  These should return non-None if they perform some decoding
    step on the file.  In this case, they must wrap and return the file-like
    object, modifying its name if appropriate.
    """
    
    def __init__(self,openers=(),decoders=()):
        self.openers = [o for o in openers]
        self.decoders = [d for d in decoders]
    
    def __call__(self,filename,mode="r"):
        # Validate the mode string
        for c in mode:
            if c not in ("r","w","a",):
                raise ValueError("Unexpected mode character: '%s'" % (c,))
        # Open the file
        for o in self.openers:
            try:
                f = o(filename,mode)
            except IOError:
                f = None
            if f is not None:
                break
        else:
            raise IOError("Could not open file %s in mode '%s'" \
                                                        %(filename,mode))
        # Decode the file as many times as required
        goAgain = True
        while goAgain:
            for d in self.decoders:
                res = d(f)
                if res is not None:
                    f = res
                    break
            else:
                goAgain = False
        # Return the final file object
        return f

##  Create default Opener that uses urllib2.urlopen() and file() as openers
def _urllib_opener(filename,mode):
    if mode != "r":
        return None
    comps = urlparse.urlparse(filename)
    # ensure it's a URL
    if comps[0] == "":
        return None
    f = urllib2.urlopen(filename)
    f.name = f.geturl()
    f.mode = mode
    return f
def _file_opener(filename,mode):
    # Dont open URLS as local files
    comps = urlparse.urlparse(filename)
    if comps[0] != "":
        return None
    return file(filename,mode)

open = Opener(openers=(_urllib_opener,_file_opener))


def is_filelike(obj,mode="rw"):
    """Test whether an object implements the file-like interface.
    
    'obj' must be the object to be tested, and 'mode' a file access
    mode such as "r", "w" or "rw".  This function returns True if 
    the given object implements the full reading/writing interface
    as required by the given mode, and False otherwise.
    
    If 'mode' is not specified, it deaults to "rw" - that is,
    checking that the full file interface is supported.
    
    This method is not intended for checking basic functionality such as
    existance of read(), but for ensuring the richer interface is
    available.  If only read() or write() is needed, it's probably
    simpler to (a) catch the AttributeError, or (b) use to_filelike(obj)
    to ensure a suitable object.
    """
    # Check reading interface
    if "r" in mode:
        # Special-case for FileLikeBase subclasses
        if isinstance(obj,FileLikeBase):
            if not hasattr(obj,"_read"):
                return False
            if obj._read.im_class is FileLikeBase:
                return False
        else:
            attrs = ("read","readline","readlines","__iter__",)
            for a in attrs:
                if not hasattr(obj,a):
                    return False
    # Check writing interface
    if "w" in mode or "a" in mode:
        # Special-case for FileLikeBase subclasses
        if isinstance(obj,FileLikeBase):
            if not hasattr(obj,"_write"):
                return False
            if obj._write.im_class is FileLikeBase:
                return False
        else:
            attrs = ("write","writelines","close")
            for a in attrs:
                if not hasattr(obj,a):
                    return False
    # Check for seekability
    if "-" not in mode:
        if isinstance(obj,FileLikeBase):
            if not hasattr(obj,"_seek"):
                return False
            if obj._seek.im_class is FileLikeBase:
                return False
        else:
            attrs = ("seek","tell",)
            for a in attrs:
                if not hasattr(obj,a):
                    return False
    return True


class join(FileLikeBase):
    """Class concatenating several file-like objects into a single file.

    This class is similar in spirit to the unix `cat` command, except that
    it produces a file-like object that is readable, writable and seekable
    (so long as the underlying files permit those operations, of course).

    When reading, data is read from each file in turn until it has been
    exhausted.  Seeks and tells are calculated using the individual positions
    of each file.

    When writing, data is spread across each file according to its size,
    and only the last file in the sequence will grow as data is appended.
    This requires that the size of each file can be determined, either by
    checking for a 'size' attribute or using seek/tell.
    """

    def __init__(self,files,mode=None):
        """Filelike join constructor.

        This first argument must be a sequence of file-like objects
        that are to be joined together.  The optional second argument
        specifies the access mode and can be used e.g. to prevent
        writing even when the underlying files are writable.
        """
        super(join,self).__init__()
        if mode:
            self.mode = mode
        self._files = list(files)
        self._curFile = 0
        if mode and "a" in mode:
            self.seek(0,2)

    def close(self):
        super(join,self).close()
        for f in self._files:
            if hasattr(f,"close"):
                f.close()

    def flush(self):
        super(join,self).flush()
        for f in self._files:
            if hasattr(f,"flush"):
                f.flush()

    def _read(self,sizehint=-1):
        data = self._files[self._curFile].read(sizehint)
        if data == "":
            if self._curFile == len(self._files) - 1:
                return None
            else:
                self._curFile += 1
                return self._read(sizehint)
        else:
            return data

    def _write(self,data,flushing=False):
        cf = self._files[self._curFile]
        # If we're at the last file, just write it all out
        if self._curFile == len(self._files) - 1:
            cf.write(data)
            return None
        # Otherwise, we may need to write into multiple files
        pos = cf.tell()
        try:
            size = cf.size
        except AttributeError:
            cf.seek(0,2)
            size = cf.tell()
            cf.seek(pos,0)
        # If the data will all fit in the current file, just write it
        gap = size - pos
        if gap >= len(data):
            cf.write(data)
            return None
        # Otherwise, split up the data and recurse
        cf.write(data[:gap])
        self._curFile += 1
        return self._write(data[gap:],flushing=flushing)

    def _seek(self,offset,whence):
        # Seek-from-end simulated using seek-to-end, then relative seek.
        if whence == 2:
            for f in self._files[self._curFile:]:
                f.seek(0,2)
            self._curFile = len(self._files)-1
            self._seek(offset,1)
        # Absolute seek simulated using tell() and relative seek.
        elif whence == 0:
            offset = offset - self._tell()
            self._seek(offset,1)
        # Relative seek
        elif whence == 1:
            # Working backwards, we simply rewind each file until
            # the offset is small enough to be within the current file
            if offset < 0:
                off1 = self._files[self._curFile].tell()
                while off1 < -1*offset:
                    offset += off1
                    self._files[self._curFile].seek(0,0)
                    # If seeking back past start of first file, stop at zero
                    if self._curFile == 0:
                        return None
                    self._curFile -= 1
                    off1 = self._files[self._curFile].tell()
                self._files[self._curFile].seek(offset,1)
            # Working forwards, we wind each file forward to its end,
            # then seek backwards once we've gone too far.
            elif offset > 0:
                offset += self._files[self._curFile].tell()
                self._files[self._curFile].seek(0,2)
                offset -= self._files[self._curFile].tell()
                while offset > 0:
                    self._curFile += 1
                    self._files[self._curFile].seek(0,2)
                    offset -= self._files[self._curFile].tell()
                self.seek(offset,1)

    def _tell(self):
        return sum([f.tell() for f in self._files[:self._curFile+1]])
 

def slice(f,start=0,stop=None,mode=None,resizable=False):
    """Manipulate a portion of a file-like object.

    This function simply exposes the class filelike.wrappers.Slice
    at the top-level of the module, since it has a nice symmetry
    with the 'join' operation.
    """
    return filelike.wrappers.Slice(f,start,stop,mode,resizable)


def to_filelike(obj,mode="r+"):
    """Convert 'obj' to a file-like object if possible.
    
    This method takes an arbitrary object 'obj', and attempts to
    wrap it in a file-like interface.  This will results in the
    object itself if it is already file-like, or some sort of
    wrapper class otherwise.
    
    'mode', if provided, should specify how the resulting object
    will be accessed.
    
    If the object cannot be converted, ValueError is raised.
    """
    # File-like objects are sutiable on their own
    if is_filelike(obj,mode):
        return obj
    # Strings can be wrapped using StringIO
    if isinstance(obj,basestring):
        return StringIO(obj)
    # Anything with read() and/or write() can be trivially wrapped
    hasRead = hasattr(obj,"read")
    hasWrite = hasattr(obj,"write")
    hasSeek = hasattr(obj,"seek")
    if "r" in mode:
        if "w" in mode or "a" in mode or "+" in mode:
            if hasRead and hasWrite and hasSeek:
                return filelike.wrappers.FileWrapper(obj)
        elif "-" not in mode:
            if hasRead and hasSeek:
                return filelike.wrappers.FileWrapper(obj)
        else:
            if hasRead:
                return filelike.wrappers.FileWrapper(obj)
    if "w" in mode or "a" in mode:
        if "-" not in mode:
            if hasWrite and hasSeek:
                return filelike.wrappers.FileWrapper(obj)
        elif hasWrite:
            return filelike.wrappers.FileWrapper(obj)
    # TODO: lots more could be done here...
    raise ValueError("Could not make object file-like: %s", (obj,))


##  Testcases start here


class Test_Read(unittest.TestCase):
    """Generic file-like testcases for readable files."""

    contents = "Once upon a time, in a galaxy far away,\nGuido van Rossum was a space alien."

    def makeFile(self,contents,mode):
        """This method must create a file of the type to be tested."""
        return None

    def setUp(self):
        self.file = self.makeFile(self.contents,"r")

    def tearDown(self):
        self.file.close()

    def test_read_all(self):
        c = self.file.read()
        self.assertEquals(c,self.contents)

    def test_read_size(self):
        c = self.file.read(5)
        self.assertEquals(c,self.contents[:5])
        c = self.file.read(7)
        self.assertEquals(c,self.contents[5:12])

    def test_readline(self):
        c = self.file.readline()
        if self.contents.find("\n") < 0:
            extra = ""
        else:
            extra = "\n"
        self.assertEquals(c,self.contents.split("\n")[0]+extra)

    def test_readlines(self):
        cs = [ln.strip("\n") for ln in self.file.readlines()]
        self.assertEquals(cs,self.contents.split("\n"))

    def test_xreadlines(self):
        cs = [ln.strip("\n") for ln in self.file.xreadlines()]
        self.assertEquals(cs,self.contents.split("\n"))

    def test_read_empty_file(self):
        f = self.makeFile("","r")
        self.assertEquals(f.read(),"")

    def test_eof(self):
        self.file.read()
        self.assertEquals(self.file.read(),"")
        self.assertEquals(self.file.read(),"")


class Test_ReadWrite(Test_Read):
    """Generic file-like testcases for writable files."""

    def setUp(self):
        self.file = self.makeFile(self.contents,"r+")

    def test_write_read(self):
        self.file.write("hello")
        c = self.file.read()
        self.assertEquals(c,self.contents[5:])

    def test_read_write_read(self):
        c = self.file.read(5)
        self.assertEquals(c,self.contents[:5])
        self.file.write("hello")
        c = self.file.read(5)
        self.assertEquals(c,self.contents[10:15])


class Test_ReadWriteSeek(Test_ReadWrite):
    """Generic file-like testcases for seekable files."""

    def test_seek_tell(self):
        self.assertEquals(self.file.tell(),0)
        self.file.seek(7)
        self.assertEquals(self.file.tell(),7)
        self.assertEquals(self.file.read(),self.contents[7:])
        self.file.seek(0,0)
        self.assertEquals(self.file.tell(),0)

    def test_read_write_seek(self):
        c = self.file.read(5)
        self.assertEquals(c,self.contents[:5])
        self.file.write("hello")
        self.file.seek(0)
        self.assertEquals(self.file.tell(),0)
        c = self.file.read(10)
        self.assertEquals(c,self.contents[:5] + "hello")

    def test_seek_cur(self):
        self.assertEquals(self.file.tell(),0)
        self.file.seek(7,1)
        self.assertEquals(self.file.tell(),7)
        self.file.seek(7,1)
        self.assertEquals(self.file.tell(),14)
        self.file.seek(-5,1)
        self.assertEquals(self.file.tell(),9)

    def test_seek_end(self):
        self.assertEquals(self.file.tell(),0)
        self.file.seek(-7,2)
        self.assertEquals(self.file.tell(),len(self.contents)-7)
        self.file.seek(3,1)
        self.assertEquals(self.file.tell(),len(self.contents)-4)


class Test_StringIO(Test_ReadWriteSeek):
    """Run our testcases against StringIO."""

    def makeFile(self,contents,mode):
        f = StringIO(contents)
        f.seek(0)
        def xreadlines():
            for ln in f.readlines():
                yield ln
        f.xreadlines = xreadlines
        return f


class Test_TempFile(Test_ReadWriteSeek):
    """Run our testcases against tempfile.TemporaryFile."""

    def makeFile(self,contents,mode):
        f = tempfile.TemporaryFile()
        f.write(contents)
        f.seek(0)
        return f


class Test_Join(Test_ReadWriteSeek):
    """Run our testcases against filelike.join."""

    def makeFile(self,contents,mode):
        files = []
        files.append(StringIO(contents[0:5]))
        files.append(StringIO(contents[5:8]))
        files.append(StringIO(contents[8:]))
        return join(files)


class Test_IsTo(unittest.TestCase):
    """Tests for is_filelike/to_filelike."""

    def test_isfilelike(self):
        """Test behaviour of is_filelike."""
        self.assert_(is_filelike(tempfile.TemporaryFile()))
        self.assert_(is_filelike(tempfile.TemporaryFile("r"),"r"))
        self.assert_(is_filelike(tempfile.TemporaryFile("r"),"w"))
        self.assert_(is_filelike(StringIO()))

    def test_tofilelike_read(self):
        """Test behavior of to_filelike for mode "r-"."""
        class F:
            def read(self,sz=-1):
                return ""
        f = to_filelike(F(),"r-")
        self.assertEquals(f.__class__,wrappers.FileWrapper)
        self.assertEquals(f.read(),"")
        self.assertRaises(ValueError,to_filelike,F(),"r")
        self.assertRaises(ValueError,to_filelike,F(),"w-")
        self.assertRaises(ValueError,to_filelike,F(),"rw")

    def test_tofilelike_readseek(self):
        """Test behavior of to_filelike for mode "r"."""
        class F:
            def read(self,sz=-1):
                return ""
            def seek(self,offset,whence):
                pass
        f = to_filelike(F(),"r")
        self.assertEquals(f.__class__,wrappers.FileWrapper)
        self.assertEquals(f.read(),"")
        self.assertRaises(ValueError,to_filelike,F(),"w")
        self.assertRaises(ValueError,to_filelike,F(),"w-")
        self.assertRaises(ValueError,to_filelike,F(),"rw")

    def test_tofilelike_write(self):
        """Test behavior of to_filelike for mode "w-"."""
        class F:
            def write(self,data):
                pass
        f = to_filelike(F(),"w-")
        self.assertEquals(f.__class__,wrappers.FileWrapper)
        self.assertRaises(ValueError,to_filelike,F(),"w")
        self.assertRaises(ValueError,to_filelike,F(),"r")
        self.assertRaises(ValueError,to_filelike,F(),"r-")
        self.assertRaises(ValueError,to_filelike,F(),"rw")

    def test_tofilelike_writeseek(self):
        """Test behavior of to_filelike for mode "w"."""
        class F:
            def write(self,data):
                pass
            def seek(self,offset,whence):
                pass
        f = to_filelike(F(),"w")
        self.assertEquals(f.__class__,wrappers.FileWrapper)
        self.assertRaises(ValueError,to_filelike,F(),"r")
        self.assertRaises(ValueError,to_filelike,F(),"r-")

    def test_tofilelike_readwrite(self):
        """Test behavior of to_filelike for mode "rw"."""
        class F:
            def write(self,data):
                pass
            def read(self,sz=-1):
                return ""
            def seek(self,offset,whence):
                pass
        f = to_filelike(F(),"rw")
        self.assertEquals(f.__class__,wrappers.FileWrapper)
        self.assertEquals(f.read(),"")

    def test_tofilelike_stringio(self):
        """Test behaviour of to_filelike on StringIO instances."""
        f = to_filelike(StringIO())
        self.assert_(isinstance(f,StringIO))

    def test_tofilelike_string(self):
        """Test behaviour of to_filelike on strings."""
        f = to_filelike("testing")
        self.assert_(isinstance(f,StringIO))
        self.assertEquals(f.read(),"testing")
        

class Test_Docs(unittest.TestCase):
    """Unittests for our documentation."""

    def test_readme(self):
        """Check that README.txt is up-to-date."""
        import os
        import difflib
        readme = os.path.join(os.path.dirname(__file__),"..","README.txt")
        if os.path.exists(readme):
            diff = difflib.unified_diff(open(readme).readlines(),__doc__.splitlines(True))
            diff = "".join(diff)
            if diff:
                print diff
                raise RuntimeError


# Included here to avoid circular includes
import filelike.wrappers

def testsuite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(Test_StringIO))
    suite.addTest(unittest.makeSuite(Test_TempFile))
    suite.addTest(unittest.makeSuite(Test_Join))
    suite.addTest(unittest.makeSuite(Test_IsTo))
    from filelike import wrappers
    suite.addTest(wrappers.testsuite())
    from filelike import pipeline
    suite.addTest(pipeline.testsuite())
    suite.addTest(unittest.makeSuite(Test_Docs))
    return suite

# Run regression tests when called from comand-line
if __name__ == "__main__":
    unittest.TextTestRunner().run(testsuite())

