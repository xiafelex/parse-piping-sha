"""Observed Isogen POD binary framing. Unknown bytes are retained, never guessed.

Four UAE samples have balanced A11A5000 / A11A50FF nested records. The uint32
after the start marker is called tag here: its meaning (class/version) is not
globally established. Framing recovery is not complete semantic decoding.
"""
from __future__ import annotations
import base64
import re
import struct
from dataclasses import dataclass, field

START = bytes.fromhex('00501aa1')
END = bytes.fromhex('ff501aa1')


@dataclass
class Node:
    start: int
    tag: int
    end: int = 0
    parent: int | None = None
    children: list = field(default_factory=list)

    def spans(self):
        cursor = self.start + 8
        for child in self.children:
            if cursor < child.start:
                yield cursor, child.start
            cursor = child.end
        if cursor < self.end - 4:
            yield cursor, self.end - 4

    def head(self, data):
        return data[self.start + 8:self.children[0].start if self.children else self.end - 4]


def parse_tree(data: bytes) -> tuple[Node, list[Node]]:
    stack, nodes = [], []
    for match in re.finditer(rb'[\x00\xff]P\x1a\xa1', data):
        offset = match.start()
        if data[offset:offset + 4] == START:
            if offset + 8 > len(data):
                raise ValueError(f'truncated start record at {offset}')
            node = Node(offset, struct.unpack_from('<I', data, offset + 4)[0], parent=stack[-1].start if stack else None)
            if stack:
                stack[-1].children.append(node)
            stack.append(node)
            nodes.append(node)
        else:
            if not stack:
                raise ValueError(f'orphan end at {offset}')
            node = stack.pop()
            node.end = offset + 4
    if stack or not nodes or nodes[0].start != 0 or nodes[0].end != len(data):
        raise ValueError('unbalanced markers or bytes outside root; unsupported framing')
    for node in nodes:
        if node.end < node.start + 12 or any(c.start < node.start + 8 or c.end > node.end - 4 for c in node.children):
            raise ValueError('overlapping marker/header; unsupported framing')
    return nodes[0], nodes


def lossless_tree(node: Node, data: bytes) -> dict:
    parts, cursor = [], node.start + 8
    for child in node.children:
        if cursor < child.start:
            parts.append({'offset': cursor, 'raw_base64': base64.b64encode(data[cursor:child.start]).decode()})
        parts.append({'object': lossless_tree(child, data)})
        cursor = child.end
    if cursor < node.end - 4:
        parts.append({'offset': cursor, 'raw_base64': base64.b64encode(data[cursor:node.end - 4]).decode()})
    return {'offset': node.start, 'end': node.end, 'tag': node.tag, 'parts': parts}


def rebuild_tree(tree: dict) -> bytes:
    return START + struct.pack('<I', tree['tag']) + b''.join(rebuild_tree(p['object']) if 'object' in p else base64.b64decode(p['raw_base64'], validate=True) for p in tree['parts']) + END


class Reader:
    def __init__(self, data: bytes, offset=0):
        self.data, self.pos = data, offset

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError(f'truncated field at {self.pos}, size={size}')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def u8(self): return self.take(1)[0]
    def u16(self): return struct.unpack('<H', self.take(2))[0]
    def u32(self): return struct.unpack('<I', self.take(4))[0]
    def i32(self): return struct.unpack('<i', self.take(4))[0]
    def f64(self): return struct.unpack('<d', self.take(8))[0]

    def string(self):
        """Observed uint32 allocated byte length, UTF16LE, null + optional padding."""
        length = self.u32()
        if length < 2 or length % 2:
            raise ValueError(f'invalid UTF16 byte length {length}')
        text = self.take(length).decode('utf-16le')
        if '\x00' not in text:
            raise ValueError('missing UTF16 terminator')
        value, _, padding = text.partition('\x00')
        if padding.strip('\x00'):
            raise ValueError('non-null string padding')
        return value


def length_strings(data: bytes, base=0):
    """Evidence scan only; strict length/terminator/printability validation."""
    out, pos = [], 0
    while pos + 6 <= len(data):
        reader = Reader(data, pos)
        try:
            length = struct.unpack_from('<I', data, pos)[0]
            if not 4 <= length <= min(200000, len(data) - pos - 4):
                raise ValueError('length')
            value = reader.string()
            if not value or not all(c.isprintable() or c in '\r\n\t' for c in value):
                raise ValueError('not printable')
            out.append({'offset': base + pos, 'end': base + reader.pos, 'value': value})
            pos = reader.pos
        except (ValueError, UnicodeError):
            pos += 1
    return out
