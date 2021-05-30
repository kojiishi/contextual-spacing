#!/usr/bin/env python3
import argparse
import itertools
import logging
import pathlib
import sys

from fontTools.ttLib import newTable
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables
from fontTools.ttLib.ttCollection import TTCollection
import uharfbuzz as hb

logger = logging.getLogger('font')


class Font(object):
    def __init__(self):
        pass

    @staticmethod
    def load(path):
        logger.info("Reading font file: \"%s\"", path)
        if isinstance(path, str):
            path = pathlib.Path(path)
        self = Font()
        self._byte_array = None
        self.font_index = None
        self._hbfont = None
        self.is_vertical = False
        self._language = None
        self.parent_collection = None
        self.path = path
        self._units_per_em = None
        self._vertical_font = None
        if self.path.suffix.casefold() == ".ttc".casefold():
            self.ttcollection = TTCollection(path, allowVID=True)
            self.ttfont = None
            self._fonts_in_collection = tuple(
                self._create_font_in_collection(index, ttfont)
                for index, ttfont in enumerate(self.ttcollection))
            logger.info("%d fonts found in the collection",
                        len(self.ttcollection))
            return self
        self.ttfont = TTFont(path, allowVID=True)
        self.ttcollection = None
        self._fonts_in_collection = None
        return self

    def _clone(self):
        clone = Font()
        clone.font_index = self.font_index
        clone._hbfont = self._hbfont
        clone.is_vertical = self.is_vertical
        clone._language = self._language
        clone.parent_collection = self.parent_collection
        clone.path = self.path
        clone.ttcollection = self.ttcollection
        clone.ttfont = self.ttfont
        clone._units_per_em = self._units_per_em
        clone._vertical_font = None
        return clone

    def _create_font_in_collection(self, font_index, ttfont):
        font = self._clone()
        font.font_index = font_index
        self._hbfont = None
        font.parent_collection = self
        font.ttfont = ttfont
        font._fonts_in_collection = None
        font.ttcollection = None
        font._vertical_font = None
        return font

    def _root_font(self, default):
        if self.is_vertical:
            return self.horizontal_font._root_font(default)
        if self.parent_collection:
            return self.parent_collection
        return default

    @property
    def root_or_self(self):
        return self._root_font(self)

    def _self_and_derived_fonts(self):
        assert self.root_or_self is self
        assert self.parent_collection is None
        assert not self.is_vertical
        yield self
        if self.is_collection:
            assert self._fonts_in_collection is not None
            yield from self._fonts_in_collection
        vertical_font = self._vertical_font
        if vertical_font:
            yield vertical_font
            if vertical_font.is_collection:
                assert vertical_font._fonts_in_collection is not None
                yield from vertical_font._fonts_in_collection

    def _set_path(self, path):
        old_path = self.path
        for font in self._self_and_derived_fonts():
            assert font.path == old_path
            font.path = path
            font._byte_array = None
            font._hbfont = None

    @property
    def fonts_in_collection(self):
        return self._fonts_in_collection

    @property
    def vertical_font(self):
        assert not self.is_vertical
        if self._vertical_font:
            assert self._vertical_font.is_vertical
            return self._vertical_font
        if not self.is_collection and not self.has_gsub_feature("vert"):
            return None
        vertical_font = self._clone()
        vertical_font.is_vertical = True
        vertical_font.horizontal_font = self
        self._vertical_font = vertical_font
        if self.is_collection:
            vertical_font._fonts_in_collection = tuple(
                font.vertical_font for font in self.fonts_in_collection)
            assert self.parent_collection is None
        elif self.parent_collection:
            vertical_font.parent_collection = self.parent_collection.vertical_font
        return vertical_font

    def save(self, out_path=None):
        if not out_path:
            out_path = pathlib.Path("out" + self.path.suffix)
        elif isinstance(out_path, str):
            out_path = pathlib.Path(out_path)
        logger.info("Saving to: \"%s\"", out_path)
        if self.ttcollection:
            for ttfont in self.ttcollection:
                self._before_save(ttfont)
            self.ttcollection.save(str(out_path))
        else:
            self._before_save(self.ttfont)
            self.ttfont.save(str(out_path))
        self._set_path(out_path)

        size_before = self.path.stat().st_size
        size_after = out_path.stat().st_size
        logger.info("File sizes: %d -> %d Delta: %d", size_before, size_after,
                    size_after - size_before)

    @staticmethod
    def _before_save(ttfont):
        # `TTFont.save()` compiles all loaded tables. Unload tables we know we did
        # not modify, so that it copies instead of re-compile.
        for key in ("CFF ", "GSUB", "name"):
            if ttfont.isLoaded(key):
                del ttfont.tables[key]

    @property
    def is_collection(self):
        return self.ttcollection is not None

    @property
    def ttfonts(self):
        if self.ttcollection:
            return self.ttcollection.fonts
        return (self.ttfont, )

    def tttable(self, name):
        return self.ttfont.get(name)

    @property
    def reader(self):
        # if self.is_collection:
        #     return self.ttfonts[0].reader
        return self.ttfont.reader

    @property
    def file(self):
        return self.reader.file

    def reader_offset(self, tag):
        entry = self.reader.tables.get(tag)
        if entry:
            return entry.offset
        return None

    @property
    def byte_array(self):
        root = self.root_or_self
        if not root._byte_array:
            root._byte_array = root.path.read_bytes()
        return root._byte_array

    @property
    def hbfont(self):
        if self._hbfont:
            return self._hbfont
        if self.is_vertical:
            return self.horizontal_font.hbfont
        byte_array = self.byte_array
        index = 0 if self.font_index is None else self.font_index
        hbface = hb.Face(byte_array, index)
        self._hbfont = hb.Font(hbface)
        return self._hbfont

    def debug_name(self, name_id):
        # name_id:
        # https://docs.microsoft.com/en-us/typography/opentype/spec/name#name-id-examples
        if self.ttfont:
            name = self.tttable("name")
            return name.getDebugName(name_id)
        return None

    def __str__(self):
        name = self.debug_name(4) or self.path.name
        attributes = []
        if self.font_index is not None:
            attributes.append(f'#{self.font_index}')
        if self.language:
            attributes.append(f'lang={self.language}')
        if self.is_vertical:
            attributes.append('vertical')
        if len(attributes):
            return f'{name} ({", ".join(attributes)})'
        return name

    @property
    def language(self):
        return self._language

    @language.setter
    def language(self, language):
        self._language = language
        if self.is_collection:
            for font in self.fonts:
                font.language = language
        assert not self.is_vertical
        if self._vertical_font:
            self._vertical_font._language = language

    @property
    def units_per_em(self):
        if self._units_per_em is None:
            self._units_per_em = self.tttable('head').unitsPerEm
        return self._units_per_em

    @property
    def script_and_langsys_tags(self, tags=("GSUB", "GPOS")):
        result = ()
        for tag in tags:
            table = self.tttable(tag)
            if not table:
                continue
            tag_result = Font.script_and_langsys_tags_for_table(table.table)
            result = itertools.chain(result, tag_result)
        return result

    @staticmethod
    def script_and_langsys_tags_for_table(table):
        scripts = table.ScriptList.ScriptRecord
        for script_record in scripts:
            script_tag = script_record.ScriptTag
            yield (script_tag, None)
            for lang_sys in script_record.Script.LangSysRecord:
                yield (script_tag, lang_sys.LangSysTag)

    def raise_require_language(self):
        raise AssertionError(
            "Need to specify the language for this font. " +
            "This font has following scripts:\n" + "\n".join(
                "  {} {}".format(t[0], "(default)" if t[1] is None else t[1])
                for t in sorted(set(self.script_and_langsys_tags),
                                key=lambda t: t[0] +
                                ("" if t[1] is None else t[1]))))

    def glyph_names(self, glyph_ids):
        ttfont = self.ttfont
        if ttfont:
            return (ttfont.getGlyphName(glyph_id) for glyph_id in glyph_ids)
        return (f'glyph{glyph_id:05}' for glyph_id in glyph_ids)

    @staticmethod
    def _has_ottable_feature(ottable, feature_tag):
        for feature_record in ottable.FeatureList.FeatureRecord:
            if feature_record.FeatureTag == feature_tag:
                return True
        return False

    @staticmethod
    def _has_tttable_feature(tttable, feature_tag):
        return (tttable
                and Font._has_ottable_feature(tttable.table, feature_tag))

    def has_gpos_feature(self, feature_tag):
        return Font._has_tttable_feature(self.tttable('GPOS'), feature_tag)

    def has_gsub_feature(self, feature_tag):
        return Font._has_tttable_feature(self.tttable('GSUB'), feature_tag)

    def add_gpos_table(self):
        logger.info("Adding GPOS table")
        ttfont = self.ttfont
        assert ttfont.get('GPOS') is None
        table = otTables.GPOS()
        table.Version = 0x00010000
        table.ScriptList = otTables.ScriptList()
        table.ScriptList.ScriptRecord = [self.create_script_record()]
        table.FeatureList = otTables.FeatureList()
        table.FeatureList.FeatureRecord = []
        table.LookupList = otTables.LookupList()
        table.LookupList.Lookup = []
        gpos = ttfont['GPOS'] = newTable('GPOS')
        gpos.table = table
        return gpos

    def create_script_record(self):
        lang_sys = otTables.LangSys()
        lang_sys.ReqFeatureIndex = 0xFFFF  # No required features
        lang_sys.FeatureIndex = []
        script = otTables.Script()
        script.DefaultLangSys = lang_sys
        script.LangSysRecord = []
        script_record = otTables.ScriptRecord()
        script_record.ScriptTag = "DFLT"
        script_record.Script = script
        return script_record

    _casefolded_font_extensions = set(ext.casefold()
                                      for ext in ('.otf', '.ttc', '.ttf'))

    @staticmethod
    def is_font_extension(extension):
        return extension.casefold() in Font._casefolded_font_extensions


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("-i", "--index", type=int, default=0)
    args = parser.parse_args()
    font = Font.load(args.path)
    font = font.fonts[args.index]
    print("debug_name:", font.debug_name)
    for tag in ("GSUB", "GPOS"):
        tttable = font.ttfont.get(tag)
        if not tttable:
            continue
        table = tttable.table
        print(
            tag + ":", ", ".join(
                set(feature_record.FeatureTag
                    for feature_record in table.FeatureList.FeatureRecord)))
        print("  " + "\n  ".join(
            str(i) for i in font.script_and_langsys_tags_for_table(table)))
