# © 2015 James R. Barlow: github.com/jbarlow83
#
# This file is part of OCRmyPDF.
#
# OCRmyPDF is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OCRmyPDF is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with OCRmyPDF.  If not, see <http://www.gnu.org/licenses/>.

"""
Generate a PDFMARK file for Ghostscript >= 9.14, for PDF/A conversion

pdfmark is an extension to the Postscript language that describes some PDF
features like bookmarks and annotations. It was originally specified Adobe
Distiller, for Postscript to PDF conversion:
https://www.adobe.com/content/dam/acom/en/devnet/acrobat/pdfs/pdfmark_reference.pdf

Ghostscript uses pdfmark for PDF to PDF/A conversion as well. To use Ghostscript
to create a PDF/A, we need to create a pdfmark file with the necessary metadata.

This takes care of the many version-specific bugs and pecularities in
Ghostscript's handling of pdfmark.

"""

from binascii import hexlify
from pathlib import Path
from string import Template
import pkg_resources
import os

import pikepdf

from pikepdf.models.metadata import (
    encode_pdf_date as _encode_date,
    decode_pdf_date as _decode_date,
)

from .helpers import deprecated


ICC_PROFILE_RELPATH = 'data/sRGB.icc'

SRGB_ICC_PROFILE = pkg_resources.resource_filename('ocrmypdf', ICC_PROFILE_RELPATH)


# This is a template written in PostScript which is needed to create PDF/A
# files, from the Ghostscript documentation. Lines beginning with % are
# comments. Python substitution variables have a '$' prefix.
pdfa_def_template = u"""%!
% Define entries in the document Info dictionary :
/ICCProfile $icc_profile
def

% Define an ICC profile :

[/_objdef {icc_PDFA} /type /stream /OBJ pdfmark
[{icc_PDFA}
<<
  /N currentpagedevice /ProcessColorModel known {
    currentpagedevice /ProcessColorModel get dup /DeviceGray eq
    {pop 1} {
      /DeviceRGB eq
      {3}{4} ifelse
    } ifelse
  } {
    (ERROR, unable to determine ProcessColorModel) == flush
  } ifelse
>> /PUT pdfmark
[{icc_PDFA} ICCProfile (r) file /PUT pdfmark

% Define the output intent dictionary :

[/_objdef {OutputIntent_PDFA} /type /dict /OBJ pdfmark
[{OutputIntent_PDFA} <<
  /Type /OutputIntent             % Must be so (the standard requires).
  /S /GTS_PDFA1                   % Must be so (the standard requires).
  /DestOutputProfile {icc_PDFA}            % Must be so (see above).
  /OutputConditionIdentifier ($icc_identifier)
>> /PUT pdfmark
[{Catalog} <</OutputIntents [ {OutputIntent_PDFA} ]>> /PUT pdfmark
"""


@deprecated
def encode_text_string(s: str) -> str:
    """
    Encode text string to hex string for use in a PDF

    From PDF 32000-1:2008 a string object may be included in hexademical form
    if it is enclosed in angle brackets.  For general Unicode the string should
    be UTF-16 (big endian) with byte order marks.  Many strings including all
    ASCII strings could be encoded as PdfDocEncoding literals provided
    that certain Postscript sequences are escaped.  But it's far simpler to
    encode everything as UTF-16.
    """

    # Sometimes lazy C programmers leave their NULs at the end of strings they
    # insert into PDFs
    # tests/resources/aspect.pdf is one example (created by ImageMagick)
    s = s.replace('\x00', '')

    if s == '':
        return ''

    utf16_bytes = s.encode('utf-16be')
    ascii_hex_bytes = hexlify(b'\xfe\xff' + utf16_bytes)
    ascii_hex_str = ascii_hex_bytes.decode('ascii').lower()
    return ascii_hex_str


def _encode_ascii(s: str) -> str:
    """
    Aggressively strip non-ASCII and PDF escape sequences

    Ghostscript 9.24+ lost support for UTF-16BE in pdfmark files for reasons
    given in GhostPDL commit e997c683. Our temporary workaround is use ASCII
    and drop all non-ASCII characters. A slightly improved alternative would
    be to implement PdfDocEncoding in pikepdf and encode to that, or handle
    metadata there.
    """
    trans = str.maketrans({'(': '', ')': '', '\\': '', '\0': ''})
    return s.translate(trans).encode('ascii', errors='replace').decode()


@deprecated
def encode_pdf_date(*args, **kwargs):
    return _encode_date(*args, **kwargs)


@deprecated
def decode_pdf_date(*args, **kwargs):
    return _decode_date(*args, **kwargs)


def _get_pdfa_def(icc_profile, icc_identifier, pdfmark=None, ascii_docinfo=None):
    """
    Create a Postscript pdfmark file for Ghostscript.

    pdfmark contains the various objects as strings; these must be encoded in
    ASCII, and dates have a special format.

    :param icc_profile: filename of the ICC profile to include in pdfmark
    :param icc_identifier: ICC identifier such as 'sRGB'
    :param pdfmark: a dictionary containing keys to include the pdfmark
    :param ascii_docinfo: parameter is no longer meaningful

    :returns: a string containing the entire pdfmark
    """

    t = Template(pdfa_def_template)
    result = t.substitute(icc_profile=icc_profile, icc_identifier=icc_identifier)
    return result


def generate_pdfa_ps(target_filename, pdfmark=None, icc='sRGB', ascii_docinfo=None):
    if icc == 'sRGB':
        icc_profile = SRGB_ICC_PROFILE
    else:
        raise NotImplementedError("Only supporting sRGB")

    # pdfmark must contain the full path to the ICC profile, and pdfmark must
    # also encoded in ASCII. ocrmypdf can be installed anywhere, including to
    # paths that have a non-ASCII character in the filename. Ghostscript
    # accepts hex-encoded strings and converts them to byte strings, so
    # we encode the path with fsencode() and use the hex representation.
    # UTF-16 not accepted here. (Even though ASCII encodable is the usual case,
    # do this always to avoid making it a rare conditional.)
    bytes_icc_profile = os.fsencode(icc_profile)
    hex_icc_profile = hexlify(bytes_icc_profile)
    icc_profile = '<' + hex_icc_profile.decode('ascii') + '>'

    ps = _get_pdfa_def(icc_profile, icc, pdfmark)

    # We should have encoded everything to pure ASCII by this point, and
    # to be safe, only allow ASCII in PostScript
    Path(target_filename).write_text(ps, encoding='ascii')


def file_claims_pdfa(filename):
    """Determines if the file claims to be PDF/A compliant

    Checking if a file is a truly compliant PDF/A is a massive undertaking
    that no open source tool does properly.  Some commercial tools are
    generally reliable (Acrobat).

    This checks if the XMP metadata contains a PDF/A marker.
    """

    pdf = pikepdf.open(filename)
    pdfmeta = pdf.open_metadata()
    if not pdfmeta.pdfa_status:
        return {
            'pass': False,
            'output': 'pdf',
            'conformance': 'No PDF/A metadata in XMP',
        }
    valid_part_conforms = {'1A', '1B', '2A', '2B', '2U', '3A', '3B', '3U'}
    conformance = 'PDF/A-{}'.format(pdfmeta.pdfa_status)
    pdfa_dict = {}
    if pdfmeta.pdfa_status in valid_part_conforms:
        pdfa_dict['pass'] = True
        pdfa_dict['output'] = 'pdfa'
    pdfa_dict['conformance'] = conformance
    return pdfa_dict
