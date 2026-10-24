"""Pinned Steam 1.0.4.0 inline contracts; data only, no process access.

Addresses are preferred VAs for image base0x120000. The installer must translate
all addresses, verify complete expected bytes, and commit the required group
atomically. Development-only refund routing is excluded from the source-union pilot.
"""
import struct

def _site(index,name,start,continuation,expected,*,post='',emulate=False,reject=0,call=0,pilot=True):
    raw=bytes.fromhex(expected)
    return {'site':index,'name':name,'source':start,'continuation':continuation,
            'expected':raw,'stolen':b'' if emulate or index==11 else raw[:-len(bytes.fromhex(post))] if post else raw,
            'post':bytes.fromhex(post),'emulate':emulate,'reject':reject,'fallbackCall':call,'pilot':pilot}

def _call(source,target):return (b'\xe8'+struct.pack('<i',target-source-5)).hex()

SITES=[
    _site(0,'geometry',0x55B47D,0x55B484,'488d0cc1488b01'),
    _site(1,'refresh_layout',0x55C0A8,0x55C0B0,'488b94c2a0f1d902'),
    _site(2,'extent_layout',0x55E328,0x55E32D,'498b54c500'),
    _site(3,'cursor_remembered',0x55E75F,0x55E764,'4d8b4cc500'),
    _site(4,'cursor_fallback',0x55E85F,0x55E864,'498b4cc500'),
    _site(5,'party_cache',0x55F8DF,0x55F8E7,'4c8d3ccb488b0ccb'),
    _site(6,'party_preview',0x56077E,0x560785,'488d04c1488b08'),
    _site(7,'find_coordinate',0x5600EF,0x5600F8,'4d8b0cce450fb75104'),
    _site(8,'find_selected',0x560157,0x560160,'4d8b0cce410fb75104'),
    _site(9,'refund_union',0x3289CF,0x3289D8,'4c8d24c166413b5006',post='66413b5006',pilot=False),
    _site(10,'refresh_begin',0x55BFF0,0x55BFF6,'405541544155'),
    _site(11,'refresh_end',0x55C453,0x55B310,'e9b8eeffff'),
    _site(12,'purchase_early',0x55D983,0x55D989,'8b8960010000',emulate=True,reject=0x55DAE5),
    _site(13,'single_tab_input',0x55C836,0x55C83B,_call(0x55C836,0x323630),emulate=True,call=0x323630),
    _site(14,'single_tab_choices',0x55EE33,0x55EE38,_call(0x55EE33,0x323630),emulate=True,call=0x323630),
    _site(15,'single_tab_header',0x56029B,0x5602A0,_call(0x56029B,0x323630),emulate=True,call=0x323630),
    _site(16,'paladin_name',0x5602F8,0x5602FD,_call(0x5602F8,0x2F9860),emulate=True,call=0x2F9860),
    _site(17,'view_close',0x55CD84,0x55CD8B,'488b8928010000',emulate=True),
]
