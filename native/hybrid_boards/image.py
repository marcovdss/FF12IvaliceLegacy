"""Validate our import-free PE and preserve RVAs in an allocation-ready image.

This is intentionally not a general-purpose PE loader. Anything requiring OS
loader fixups, TLS, exception tables, or a DLL entrypoint is rejected.
"""
import struct


def require(condition,message='Invalid native-core PE'):
    if not condition:raise ValueError(message)


def mapped_image(raw, expected_exports=11):
    pe=struct.unpack_from('<I',raw,0x3c)[0]
    require(raw[pe:pe+4]==b'PE\0\0')
    require(struct.unpack_from('<H',raw,pe+4)[0]==0x8664)
    opt=pe+24
    require(struct.unpack_from('<H',raw,opt)[0]==0x20b)
    require(struct.unpack_from('<I',raw,opt+16)[0]==0,'DLL entrypoint requires OS loader')
    base=struct.unpack_from('<Q',raw,opt+24)[0]
    size,headers=struct.unpack_from('<II',raw,opt+56)
    require(0<headers<=len(raw) and headers<=size<=0x100000)
    data=bytearray(size);data[:headers]=raw[:headers]
    section=opt+struct.unpack_from('<H',raw,pe+20)[0]
    executable=[]
    for i in range(struct.unpack_from('<H',raw,pe+6)[0]):
        row=section+40*i
        virtual,rva,count,offset=struct.unpack_from('<IIII',raw,row+8)
        require(rva+max(virtual,count)<=size and offset+count<=len(raw))
        data[rva:rva+count]=raw[offset:offset+count]
        if struct.unpack_from('<I',raw,row+36)[0]&0x20000000:
            executable.append([rva,rva+virtual])
    directory=lambda i:struct.unpack_from('<II',raw,opt+112+8*i)
    # The linker emits an empty .idata descriptor; permit only its zero bytes.
    irva,isize=directory(1)
    require(not irva or (isize>=20 and irva+isize<=size and not any(data[irva:irva+isize])),'Imports require loader')
    for index,label in ((3,'unwind'),(5,'relocation'),(9,'TLS'),(10,'load-config'),(12,'IAT'),(13,'delay-import'),(14,'CLR')):
        require(directory(index)==(0,0),f'{label} directory requires loader support')
    erva,esize=directory(0)
    require(erva and esize and erva+esize<=size)
    names=struct.unpack_from('<I',data,erva+24)[0]
    frva,nrva,orva=struct.unpack_from('<III',data,erva+28)
    exports={}
    for i in range(names):
        nameptr=struct.unpack_from('<I',data,nrva+4*i)[0]
        name=data[nameptr:data.index(0,nameptr)].decode('ascii')
        ordinal=struct.unpack_from('<H',data,orva+2*i)[0]
        target=struct.unpack_from('<I',data,frva+4*ordinal)[0]
        require(not erva<=target<erva+esize,'Forwarded export requires loader')
        require(any(a<=target<b for a,b in executable),'Export outside executable code')
        require(name.startswith(('HB_', 'HBX_')) and name not in exports)
        exports[name]=target
    require(len(exports)==expected_exports)
    return bytes(data),{'preferredBase':base,'imageSize':size,'exports':exports,
                       'executableRvas':executable,'imports':0,'entrypoint':0,
                       'relocations':0,'tls':False,'unwind':False}
