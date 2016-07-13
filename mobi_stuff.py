# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__   = 'GPL v3'
__docformat__ = 'restructuredtext en'

import os
import struct
import re

import calibre_plugins.kindleunpack_plugin.config as cfg
import calibre_plugins.kindleunpack_plugin.kindleunpackcore.kindleunpack as _mu
from calibre_plugins.kindleunpack_plugin.kindleunpackcore.compatibility_utils import PY2, bstr, unicode_str
from calibre_plugins.kindleunpack_plugin.kindleunpackcore.mobi_split import mobi_split

if PY2:
    range = xrange

class SectionizerLight:
    """ Stolen from Mobi_Unpack and slightly modified. """
    def __init__(self, filename):
        self.data = open(filename, 'rb').read()
        if self.data[:2] == b'PK':
            self.ident = 'PK'
            return
        if self.data[:3] == b'TPZ':
            self.ident = 'TPZ'
        else:
            self.palmheader = self.data[:78]
            self.ident = self.palmheader[0x3C:0x3C+8]
        try:
            self.num_sections, = struct.unpack_from(b'>H', self.palmheader, 76)
        except:
            return
        self.filelength = len(self.data)
        try:
            sectionsdata = struct.unpack_from(bstr('>%dL' % (self.num_sections*2)), self.data, 78) + (self.filelength, 0)
            self.sectionoffsets = sectionsdata[::2]
        except:
            pass

    def loadSection(self, section):
        before, after = self.sectionoffsets[section:section+2]
        return self.data[before:after]

class MobiHeaderLight:
    """ Stolen from Mobi_Unpack and slightly modified. """
    def __init__(self, sect, sectNumber):
        self.sect = sect
        self.start = sectNumber
        self.header = self.sect.loadSection(self.start)
        self.records, = struct.unpack_from(b'>H', self.header, 0x8)
        self.length, self.type, self.codepage, self.unique_id, self.version = struct.unpack(b'>LLLLL', self.header[20:40])
        self.mlstart = self.sect.loadSection(self.start+1)[0:4]
        self.crypto_type, = struct.unpack_from(b'>H', self.header, 0xC)

    def isEncrypted(self):
        return self.crypto_type != 0

    def isPrintReplica(self):
        return self.mlstart[0:4] == b'%MOP'

    # Standalone KF8 file
    def isKF8(self):
        return self.start != 0 or self.version == 8

    def isJointFile(self):
        # Check for joint MOBI/KF8
        for i in range(len(self.sect.sectionoffsets)-1):
            before, after = self.sect.sectionoffsets[i:i+2]
            if (after - before) == 8:
                data = self.sect.loadSection(i)
                if data == b'BOUNDARY' and self.version != 8:
                    return True
                    break
        return False


def makeFileNames(prefix, infile, outdir, kf8=False):
    if kf8:
        return os.path.join(outdir, prefix+os.path.splitext(os.path.basename(infile))[0] + '.azw3')
    return os.path.join(outdir, prefix+os.path.splitext(os.path.basename(infile))[0] + '.mobi')

import sys, subprocess, shutil

IS_WIN32 = 'win32' in str(sys.platform).lower()

def subprocess_call(*args, **kwargs):
    #also works for Popen. It creates a new *hidden* window, so it will work in frozen apps (.exe).
    if IS_WIN32:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags = subprocess.CREATE_NEW_CONSOLE | subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs['startupinfo'] = startupinfo
    retcode = subprocess.call(*args, **kwargs)
    return retcode
    
from functools import partial
from calibre_plugins.kindleunpack_plugin.kindleunpackcore.mobi_split import readsection, writesection, add_exth, read_exth, write_exth, writeint

def add_update_exth(mobi_header, exth_key, exth_value):
    if read_exth(mobi_header, exth_key):
        infKF8Updater = partial(write_exth)
    else:
        infKF8Updater = partial(add_exth)
    return infKF8Updater(mobi_header, exth_key, exth_value)

def amend_kf8(kf8_data):
    infKF8 = readsection(kf8_data, 0)
    #update exth set [501](cdeType)='EBOK'
    infKF8 = add_update_exth(infKF8, 501, b'EBOK')
    return writesection(kf8_data, 0, infKF8)

def write_asin_kf8(kf8_data, asin_text):
    infKF8 = readsection(kf8_data, 0)
    infKF8 = add_update_exth(infKF8, 113, asin_text)
    infKF8 = add_update_exth(infKF8, 504, asin_text)
    return writesection(kf8_data, 0, infKF8)
    
class mobiProcessor:
    def __init__(self, infile):
        self.ePubVersion = cfg.plugin_prefs['Epub_Version']
        self.useHDImages = cfg.plugin_prefs['Use_HD_Images']
        self.kindlegenPath = None
        
        self.infile = infile
        self.sect = SectionizerLight(self.infile)
        if self.sect.ident == 'PK':
            self.version = 0
            self.isEncrypted = False
            self.isPrintReplica = False
            self.isComboFile = False
            self.isKF8 = False
            self.isEpub = True
            return
            
        self.isEpub = False
        if (self.sect.ident != b'BOOKMOBI' and self.sect.ident != b'TEXtREAd') or self.sect.ident == 'TPZ':
            raise Exception(_('Unrecognized Kindle/MOBI file format!'))
        mhl = MobiHeaderLight(self.sect, 0)
        self.version = mhl.version
        self.isEncrypted = mhl.isEncrypted()
        if self.sect.ident == b'TEXtREAd':
            self.isPrintReplica = False
            self.isComboFile = False
            self.isKF8 = False
            return
        self.isPrintReplica = mhl.isPrintReplica()
        self.isKF8 = mhl.isKF8()
        self.isComboFile = mhl.isJointFile()

        self.ePubVersion = cfg.plugin_prefs['Epub_Version']
        self.useHDImages = cfg.plugin_prefs['Use_HD_Images']
        
    def getPDFFile(self, outdir):
        _mu.unpackBook(self.infile, outdir)
        files = os.listdir(outdir)
        pdf = ''
        filefilter = re.compile('\.pdf$', re.IGNORECASE)
        files = filter(filefilter.search, files)
        if files:
            for filename in files:
                pdf = os.path.join(outdir, filename)
                break
        else:
            raise Exception(_('Problem locating unpacked pdf.'))
        if pdf=='':
            raise Exception(_('Problem locating unpacked pdf.'))
        if not os.path.exists(pdf):
            raise Exception(_('Problem locating unpacked pdf: {0}'.format(pdf)))
        return pdf
        
    def getAZW3File(self, outdir):
        mobi_to_split = mobi_split(unicode_str(self.infile))
        outKF8 = makeFileNames('', self.infile, outdir, True)
        file(outKF8, 'wb').write(amend_kf8(mobi_to_split.getResult8()))
        return outKF8

    def unpackMOBI(self, outdir):
        _mu.unpackBook(self.infile, outdir, epubver=self.ePubVersion, use_hd=self.useHDImages)

    def unpackEPUB(self, outdir):
        _mu.unpackBook(self.infile, outdir, epubver=self.ePubVersion, use_hd=self.useHDImages)
        kf8dir = os.path.join(outdir, 'mobi8')
        kf8BaseName = os.path.splitext(os.path.basename(self.infile))[0]
        epub = os.path.join(kf8dir, '{0}.epub'.format(kf8BaseName))
        if not os.path.exists(epub):
            raise Exception(_('Problem locating unpacked epub: {0}'.format(epub)))
        return epub

    def writeSplitCombo(self, outdir):
        mobi_to_split = mobi_split(unicode_str(self.infile))
        outMobi = makeFileNames('MOBI-', self.infile, outdir)
        outKF8 = makeFileNames('KF8-', self.infile, outdir, True)
        file(outMobi, 'wb').write(mobi_to_split.getResult7())
        file(outKF8, 'wb').write(amend_kf8(mobi_to_split.getResult8()))

    def getKindlegen(self):
        if not self.kindlegenPath:
            self.kindlegenPath = cfg.getKindlegen()
        return self.kindlegenPath
        
    def amendAzw3(self):
        kf8file = file(self.infile, 'rb')
        kf8data = amend_kf8(kf8file.read())
        kf8file.close()
        file(self.infile, 'wb').write(kf8data)
        return self.infile
    
    def cvt2AZW3File(self, outdir):
        inBaseName = os.path.splitext(os.path.basename(self.infile))[0]
        tmpEpubFile = os.path.join(outdir, inBaseName+'.epub')
        shutil.copy(self.infile, tmpEpubFile)
        tmpMobiFile = inBaseName+'.mobi'
        kindlegen_cmdline=[self.getKindlegen(), tmpEpubFile, '-o', tmpMobiFile]
        ret = subprocess_call(kindlegen_cmdline)
        if ret > 1:
            raise Exception(_('Kindlegen({0}) exit with {1}'.format(','.join(kindlegen_cmdline), ret)))
        tmpMobiFile = os.path.join(outdir, tmpMobiFile)
        mobi_to_split = mobi_split(unicode_str(tmpMobiFile))
        outKF8 = os.path.join(outdir, inBaseName+'.azw3')
        file(outKF8, 'wb').write(amend_kf8(mobi_to_split.getResult8()))
        return outKF8
    
    def getAsin(self):
        kf8file = file(self.infile, 'rb')
        infKF8 = readsection(kf8file.read(), 0)
        asin_texts = read_exth(infKF8, 113)
        if not asin_texts:
            asin_texts = read_exth(infKF8, 504)
        if asin_texts:
            return asin_texts[0]
        return b''

    def setAsin(self, asin_text):
        kf8file = file(self.infile, 'rb')
        kf8data = write_asin_kf8(kf8file.read(), asin_text)
        kf8file.close()
        file(self.infile, 'wb').write(kf8data)
        return self.infile