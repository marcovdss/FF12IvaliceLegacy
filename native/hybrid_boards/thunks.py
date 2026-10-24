"""Emit own x64 routing thunks for previously verified inline load sites.

No process is opened or written. Entry patches must be separately byte-guarded,
transactional and lifetime-managed by a reviewed installer.
"""
import struct

FRAME=32
NEXT=392
REJECT=400
ALLOC=448

def _imm(value):return struct.pack('<Q',value)
def _mem(op,reg,offset):
    return bytes((0x48 | (4 if reg>=8 else 0),op,0x84|((reg&7)<<3),0x24))+struct.pack('<i',offset)
def _xmm(load,reg,offset):
    return b'\xf3'+(b'\x44' if reg>=8 else b'')+bytes((0x0f,0x6f if load else 0x7f,0x84|((reg&7)<<3),0x24))+struct.pack('<i',offset)

def emit(state,route,site,continuation,stolen,*,post=b'',reject=0,emulate_stolen=False,fixups=None):
    """Call route(state,site,HookFrame*) preserving all non-output CPU state.

    Ordinary displaced MOV/LEA instructions run before capture. A displaced CMP
    may be passed as post so flags reflect the substituted operand. Purchase's
    MOV is emulated by the guard and is omitted only when explicitly requested.
    """
    code=bytearray(b'' if emulate_stolen else stolen)
    def immediate(name,value):
        if fixups is not None:fixups.append({'offset':len(code),'kind':name,'size':8})
        code.extend(_imm(value))
    code+=b'\x9c\x50\x48\x89\xe0\x48\x83\xe4\xf0\x48\x81\xec'+struct.pack('<I',ALLOC)
    # RAX points to the two temporary pushes (original RAX, original RFLAGS).
    for reg in (1,2,3,5,6,7,8,9,10,11,12,13,14,15):code+=_mem(0x89,reg,FRAME+reg*8)
    code+=b'\x4c\x8d\x58\x10'+_mem(0x89,11,FRAME+4*8)
    code+=b'\x4c\x8b\x18'+_mem(0x89,11,FRAME)
    code+=b'\x4c\x8b\x58\x08'+_mem(0x89,11,FRAME+128)
    for reg in range(16):code+=_xmm(False,reg,FRAME+136+16*reg)
    code+=b'\x48\xb8';immediate('continuation',continuation);code+=_mem(0x89,0,FRAME+NEXT)
    code+=b'\x48\xb8';immediate('reject',reject);code+=_mem(0x89,0,FRAME+REJECT)
    code+=b'\xfc\x48\xb9';immediate('state',state);code+=b'\xba'+struct.pack('<I',site)
    code+=b'\x4c\x8d\x84\x24'+struct.pack('<I',FRAME)
    code+=b'\x48\xb8';immediate('route',route);code+=b'\xff\xd0'
    for reg in range(16):code+=_xmm(True,reg,FRAME+136+16*reg)
    # Stage final jump and RAX below original RSP; no register is sacrificed.
    code+=_mem(0x8b,0,FRAME+4*8)
    code+=_mem(0x8b,11,FRAME+NEXT)+b'\x4c\x89\x58\xf0'
    code+=_mem(0x8b,11,FRAME)+b'\x4c\x89\x58\xf8'
    for reg in (1,2,3,5,6,7,8,9,10,11,12,13,14,15):code+=_mem(0x8b,reg,FRAME+reg*8)
    code+=b'\xff\xb4\x24'+struct.pack('<I',FRAME+128)+b'\x9d'
    code+=b'\x48\x89\xc4\x48\x8b\x44\x24\xf8'+post+b'\xff\x64\x24\xf0'
    return bytes(code)

def entry_patch(source,target,length):
    if length<5:raise ValueError('Overwrite must hold a whole-instruction near jump')
    delta=target-(source+5)
    if not -(1<<31)<=delta<(1<<31):raise ValueError('Thunk outside rel32 reach')
    return b'\xe9'+struct.pack('<i',delta)+b'\x90'*(length-5)

def emit_site(state,route,definition,base,*,module_base=0x120000,fixups=None):
    """Emit one known site with an exact original-call fallback when required."""
    translate=lambda va:module_base+(va-0x120000) if va else 0
    continuation=translate(definition['continuation'])
    reject=translate(definition['reject'])
    kwargs={'post':definition['post'],'emulate_stolen':definition['emulate']}
    fallback=definition.get('fallbackCall',0)
    if fallback:
        provisional=emit(state,route,definition['site'],continuation,definition['stolen'],reject=0,**kwargs)
        reject=base+len(provisional)
    code=emit(state,route,definition['site'],continuation,definition['stolen'],reject=reject,fixups=fixups,**kwargs)
    if fallback:
        if fixups is not None:
            for fixup in fixups:
                if fixup['kind']=='reject':
                    fixup['kind']='fallback';fixup['targetOffset']=len(code)
        target=translate(fallback);delta=target-(base+len(code)+5)
        if not -(1<<31)<=delta<(1<<31):raise ValueError('Original call outside rel32 reach')
        if fixups is not None:fixups.append({'offset':len(code)+1,'kind':'fallbackCall','size':4})
        code+=b'\xe8'+struct.pack('<i',delta)+b'\xff\x25\0\0\0\0'+_imm(continuation)
        if fixups is not None:fixups.append({'offset':len(code)-8,'kind':'continuation','size':8})
    return code
