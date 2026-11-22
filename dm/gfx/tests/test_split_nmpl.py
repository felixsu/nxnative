"""The Nmpl engine's shaders (Village in the Shade), without game data: FAFULLFS archives (an index of entries, NMPL_FX_SHADER02 records of a vertex and a pixel shader as bare GLSLC
sections) and bare control sections in an NRO loose in the RomFS."""
from pathlib import Path
import contextlib
import io
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
_had_game = os.environ.get('DM_GAME')
os.environ['DM_GAME'] = '010015100B514000'   # the reader needs no Village in the Shade profile
import shader_split
if _had_game is None: del os.environ['DM_GAME']
else: os.environ['DM_GAME'] = _had_game

CTL, GFX = shader_split.CTL_MAGIC, shader_split.GFX_MAGIC


def control(code_len, nctl=0x880):
    c = bytearray(nctl)
    c[:4] = CTL
    struct.pack_into('<I', c, 0x20, nctl)
    struct.pack_into('<I', c, 0x704, code_len)
    return bytes(c)


def code_area(n, kind):
    """A graphics code area: the magic, and SPH word 0 (+0x30) with the shader type (1 vertex, 5 pixel) in bits 10..13"""
    c = bytearray(n)
    c[:4] = GFX
    struct.pack_into('<I', c, 0x30, kind << 10)
    return bytes(c)


def fx_record(vs_len=0x300, ps_len=0x200, names=b'POSITION0\0TEXCOORD0\0'):
    """An NMPL_FX_SHADER02 record: a header, then per stage some names, the control section and its code area right after it"""
    r = bytearray(b'NMPL_FX_SHADER02' + struct.pack('<11I', 1, 1, 1, 2, 0, 0x170, 0x2f0, 0x330, 0x370, 0x400, 0)).ljust(0x6c0, b'\0')
    for n, kind in ((vs_len, 1), (ps_len, 5)):
        r += names.ljust((len(names) + 0xf) & ~0xf, b'\0')   # the control sections are 16-byte aligned in the game's records
        r += control(n) + code_area(n, kind)
        r = r.ljust((len(r) + 0xff) & ~0xff, b'\0')
    return bytes(r)


def fafullfs(entries):
    """A FAFULLFS archive of (name, bytes) entries: a 0x200-byte header, the entries 0x200-aligned, the index of 0x30-byte entries, then the names"""
    body, index, at = bytearray(), bytearray(), 0x200
    for i, (_, data) in enumerate(entries):
        index += struct.pack('<6Q', 0x8000000000000000 | i, i, 0, len(data), at, 0x6ac324e0)
        body += data.ljust((len(data) + 0x1ff) & ~0x1ff, b'\0'); at += (len(data) + 0x1ff) & ~0x1ff
    head = b'FAFULLFS' + struct.pack('<IIQQ', len(entries), 0x204eac, len(body), len(index)) + struct.pack('<Q', at)
    return head.ljust(0x200, b'\0') + bytes(body) + bytes(index) + b''.join(n.encode() + b'\0' for n, _ in entries)


class FafullfsTests(unittest.TestCase):
    def read(self, archive):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'shader_1_00.dat'; p.write_bytes(archive)
            out, log = [], io.StringIO()
            with contextlib.redirect_stdout(log): shader_split.fafullfs_shaders(str(p), '/data/shader_1_00.dat', out)
            return out, log.getvalue()

    def test_each_record_gives_its_vertex_and_pixel_shader_as_one_variation(self):
        out, log = self.read(fafullfs([('data/shader/a.fxdat', fx_record()), ('data/shader/b.fxdat', fx_record(0x100, 0x400))]))
        self.assertEqual(log, '')
        self.assertEqual([st for _, st, _, _ in out], ['vs', 'ps', 'vs', 'ps'])
        self.assertEqual([len(code) for _, _, _, code in out], [0x300, 0x200, 0x100, 0x400])
        self.assertEqual(len({w for w, *_ in out}), 2)
        self.assertTrue(all('#NMPL@' in w and w.endswith('#var0') for w, *_ in out))
        self.assertEqual(len(shader_split.var_pairs(out)), 2)

    def test_entries_that_are_not_shader_records_are_skipped(self):
        out, log = self.read(fafullfs([('data/texture/t.tex', b'NMPLTEX1' + bytes(0x400)), ('data/shader/a.fxdat', fx_record())]))
        self.assertEqual(log, '')
        self.assertEqual(len(out), 2)

    def test_a_control_section_without_its_code_area_is_left_out_with_a_red_line(self):
        rec = bytearray(fx_record())
        k = rec.find(GFX); rec[k:k + 4] = b'\0\0\0\0'   # the vertex shader's code area loses its header
        out, log = self.read(fafullfs([('data/shader/a.fxdat', bytes(rec))]))
        self.assertEqual([st for _, st, _, _ in out], ['ps'])
        self.assertIn('[RED]', log)

    def test_an_index_past_the_end_is_refused(self):
        a = fafullfs([('data/shader/a.fxdat', fx_record())])
        with self.assertRaises(SystemExit): self.read(a[:-0x40])

    def test_scan_file_dispatches_on_the_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'shader_1_00.dat'; p.write_bytes(fafullfs([('data/shader/a.fxdat', fx_record())]))
            rel, keys, pairs, _ = shader_split.scan_file_in((str(p), 'data/shader_1_00.dat'))
        self.assertEqual((rel, len(keys), len(pairs)), ('data/shader_1_00.dat', 2, 1))


def nro(body):
    """A minimal NRO image: a branch, the 'NRO0' header at +0x10, then the body"""
    head = bytearray(0x80)
    struct.pack_into('<I', head, 4, 0x80)
    head[0x10:0x14] = b'NRO0'
    struct.pack_into('<I', head, 0x18, 0x80 + len(body))
    return bytes(head) + body


class NroTests(unittest.TestCase):
    def scan(self, data, name='NmplDLL.nro'):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / name; p.write_bytes(data)
            with contextlib.redirect_stdout(io.StringIO()):
                return shader_split.scan_file_in((str(p), name))

    def test_a_loose_nro_is_read_for_bare_control_sections(self):
        code = code_area(0x200, 5)   # nothing references it: the code area that ends right at its control section, at the length that gives
        rel, keys, pairs, _ = self.scan(nro(bytes(0x40) + code + control(len(code))))
        self.assertEqual((rel, len(keys), pairs), ('NmplDLL.nro', 1, set()))

    def test_an_nro_without_shaders_gives_none(self):
        self.assertEqual(self.scan(nro(bytes(0x1000)), 'lz4.nro')[1], {})

    def test_bare_sections_in_a_file_that_is_not_an_nro_are_not_read(self):
        code = code_area(0x200, 5)
        self.assertEqual(self.scan(bytes(0x80) + code + control(len(code)), 'blob.bin')[1], {})


if __name__ == '__main__':
    unittest.main()
