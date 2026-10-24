// Native routing layer. Caller-owned state; no process APIs or live hook installer.
// Pointer arguments are trusted mapped engine/owned ranges supplied by a supervisor.
#include "core.cpp"

struct HookFrame { uint64_t r[16],flags; uint8_t xmm[16][16]; uint64_t next,reject; };
enum Reg { AX,CX,DX,BX,SP,BP,SI,DI,R8,R9,R10,R11,R12,R13,R14,R15 };
enum Site { Geometry,Refresh,Extent,Remembered,Fallback,Party,Preview,Find,FindSelected,Refund,BeginRefresh,EndRefresh,Purchase,TabToggle,TabChooser,TabHeader,Name,Close,SiteCount };
struct ActorContext { uint64_t menu,keep; uint32_t actor,pair; };
struct AdapterRequest { Request request; uint64_t keepRootSlot; };
struct CacheBinding {
    uint64_t slot,menu,keep,board; uint32_t actor,pair,generation,ordinal;
    uint8_t snapshot[0x1c8];
};
struct ViewBinding {
    uint64_t view,menu,keep,tiles;uint32_t actor,pair,generation,fresh;
    uint8_t snapshot[0x1c8];
};
struct Adapter {
    uint32_t magic,enabled,busy,reserved;
    State core;
    uint64_t nativeBoards,nativeGuard,keepRootSlot;
    CacheBinding caches[6];
    ViewBinding views[8];
    Board emptyBoard;
    uint64_t emptyPointer;
    Board refundBoards[6];
    uint64_t refundPointers[6];
    uint8_t paladin[8];
    uint32_t calls[SiteCount],routed[SiteCount],rejected[SiteCount];
};
using NativeGuard=uint32_t(*)(uint32_t,uint32_t,uint32_t);
using NativeDecode=uint64_t(*)(uint32_t);
constexpr uint32_t ADAPTER_MAGIC=0x48425831;
static bool currentCost(Adapter*a,uint32_t id,uint32_t&cost){
    // Same live table/header and decoder used by the pinned native guard.
    // A stale or modified UI price must never authorize a cheaper purchase.
    uint64_t headerSlot=a->nativeGuard+(0x2EBF038ull-0x323600ull);
    uint64_t header=*reinterpret_cast<const uint64_t*>(headerSlot);
    if(!header||id>360||*reinterpret_cast<const uint16_t*>(header+8)!=24)return false;
    uint64_t decoder=a->nativeGuard-(0x323600ull-0x20E600ull);
    uint64_t rows=reinterpret_cast<NativeDecode>(decoder)(dword(header,12));
    if(!rows)return false;
    cost=byte(rows,uint32_t(id*24+4));return true;
}
static bool equal(const uint8_t*a,const uint8_t*b,uint32_t count){
    for(uint32_t i=0;i<count;i++)if(a[i]!=b[i])return false;
    return true;
}
static bool populated(const Board&b){
    if(b.tag!=0x6463696c||b.width!=24||b.height!=24)return false;
    for(uint32_t i=0;i<576;i++)if(b.ids[i]!=EMPTY)return true;
    return false;
}
static bool ownedNeighbor(const Board&b,uint64_t keep,uint32_t x,uint32_t y){
    for(uint32_t direction=0;direction<4;direction++){
        int32_t nx=int32_t(x),ny=int32_t(y);
        if(direction==0)--nx;else if(direction==1)++nx;else if(direction==2)--ny;else ++ny;
        if(nx<0||ny<0||nx>=24||ny>=24)continue;
        uint32_t id=b.ids[uint32_t(ny)*24+uint32_t(nx)];
        if(id<=360&&(byte(keep,0x194+id/8)&(1u<<(id%8))))return true;
    }
    return false;
}
static bool rootCurrent(Adapter*a){
    if(!a)return false;
    State&s=a->core;
    bool ok=a->magic==ADAPTER_MAGIC&&a->enabled&&s.magic==MAGIC&&s.objectCount&&
        s.rootSlot&&s.resolver&&s.keepRoot&&
        *reinterpret_cast<const uint64_t*>(s.rootSlot)==s.rootValue;
    if(!ok||!a->keepRootSlot){
        for(uint32_t i=0;i<6;i++)a->caches[i].generation=0;
        for(uint32_t i=0;i<8;i++)a->views[i].fresh=0;
        return false;
    }
    uint64_t root=*reinterpret_cast<const uint64_t*>(a->keepRootSlot);
    if(!root){
        for(uint32_t i=0;i<6;i++)a->caches[i].generation=0;
        for(uint32_t i=0;i<8;i++)a->views[i].fresh=0;
        return false;
    }
    if(root!=s.keepRoot){
        s.keepRoot=root;
        for(uint32_t i=0;i<6;i++)a->caches[i].generation=0;
        for(uint32_t i=0;i<8;i++)a->views[i].fresh=0;
    }
    return true;
}
static bool menuContext(Adapter*a,uint64_t menu,ActorContext&out){
    if(!rootCurrent(a)||!menu)return false;
    State&s=a->core;bool found=false;
    for(uint32_t i=0;i<s.objectCount;i++){
        if(reinterpret_cast<Resolver>(s.resolver)(int32_t(i))==menu){found=true;break;}
    }
    if(!found)return false;
    uint32_t actor=byte(menu,0x60);
    if(actor>5)return false;
    uint64_t keep=s.keepRoot+8+uint64_t(actor)*0x1c8;
    uint32_t j1=byte(keep,0x1c3),j2=byte(keep,0x1c4),selected=byte(keep,0x1c5);
    uint32_t pair=pairkey(j1,j2);
    if(byte(keep,4)!=actor||byte(keep,5)||pair>=66||
       (selected!=j1&&selected!=j2)||!s.layouts[pair].generation||
       !populated(s.layouts[pair].board))return false;
    out={menu,keep,actor,pair};return true;
}
static bool viewContext(Adapter*a,uint64_t view,ActorContext&out){
    if(!rootCurrent(a)||!view)return false;
    int32_t handle=int32_t(dword(view,0x160));
    if(handle<0||uint32_t(handle)>=a->core.objectCount)return false;
    uint64_t menu=reinterpret_cast<Resolver>(a->core.resolver)(handle);
    return menuContext(a,menu,out);
}
static bool keepContext(Adapter*a,uint64_t keep,ActorContext&out){
    if(!rootCurrent(a)||!keep)return false;
    uint32_t actor=byte(keep,4);
    if(actor>5||keep!=a->core.keepRoot+8+uint64_t(actor)*0x1c8)return false;
    for(uint32_t i=0;i<a->core.objectCount;i++){
        uint64_t menu=reinterpret_cast<Resolver>(a->core.resolver)(int32_t(i));
        if(menu&&byte(menu,0x60)==actor)return menuContext(a,menu,out);
    }
    return false;
}
static bool refundUnion(Adapter*a,const ActorContext&ctx){
    bool seen[361];uint32_t count=0;
    for(uint32_t i=0;i<361;i++)reinterpret_cast<volatile bool*>(seen)[i]=false;
    Board&out=a->refundBoards[ctx.actor];
    uint32_t jobs[2]={byte(ctx.keep,0x1c3),byte(ctx.keep,0x1c4)};
    for(uint32_t which=0;which<3;which++){
        const Board*b=which==2?&a->core.layouts[ctx.pair].board:
            reinterpret_cast<const Board*>(reinterpret_cast<const uint64_t*>(a->nativeBoards)[jobs[which]]);
        if(!b||b->tag!=0x6463696c||!b->width||!b->height||b->width>24||b->height>24)return false;
        uint32_t cells=uint32_t(b->width)*b->height;
        for(uint32_t i=0;i<cells;i++){
            uint32_t id=b->ids[i];if(id==EMPTY)continue;
            if(id>360)return false;
            if(!seen[id]){seen[id]=true;++count;}
        }
    }
    if(!count)return false;
    out.tag=0x6463696c;out.width=24;out.height=24;
    uint32_t cursor=0;
    for(uint32_t id=0;id<361;id++)if(seen[id])out.ids[cursor++]=uint16_t(id);
    while(cursor<576)out.ids[cursor++]=uint16_t(EMPTY);
    a->refundPointers[ctx.actor]=reinterpret_cast<uint64_t>(&out);return true;
}
API uint32_t HBX_size(){return sizeof(Adapter);}
API uint32_t HBX_init(Adapter*a,uint32_t size){
    if(!a||size!=sizeof(Adapter))return 0;
    for(uint32_t i=0;i<size;i++)reinterpret_cast<volatile uint8_t*>(a)[i]=0;
    a->magic=ADAPTER_MAGIC;a->emptyBoard.tag=0x6463696c;
    a->emptyPointer=reinterpret_cast<uint64_t>(&a->emptyBoard);
    const uint8_t name[8]={0x2f,0x3a,0x45,0x3a,0x3d,0x42,0x47,0};
    for(uint32_t i=0;i<8;i++)a->paladin[i]=name[i];
    return HB_init(&a->core,sizeof(State));
}
API uint64_t HBX_core(Adapter*a){return a&&a->magic==ADAPTER_MAGIC?reinterpret_cast<uint64_t>(&a->core):0;}
API uint32_t HBX_config(Adapter*a,const AdapterRequest*config,uint64_t nativeBoards,uint64_t guard){
    if(!a||a->magic!=ADAPTER_MAGIC)return 0;
    const Request*r=config?&config->request:nullptr;
    a->enabled=0;
    for(uint32_t i=0;i<6;i++)a->caches[i].generation=0;
    for(uint32_t i=0;i<8;i++)a->views[i].fresh=0;
    // Count is a trusted supervisor bound, not an inferred native engine limit.
    if(!r||!r->objectCount||r->objectCount>9||!nativeBoards||!HB_config(&a->core,r))return 0;
    uint64_t keepSlot=config->keepRootSlot;
    if(!keepSlot||*reinterpret_cast<const uint64_t*>(keepSlot)!=r->keepRoot)return 0;
    if(!guard)return 0;
    a->nativeBoards=nativeBoards;a->nativeGuard=guard;a->keepRootSlot=keepSlot;a->enabled=1;return 1;
}
API uint32_t HBX_preflight(Adapter*a,uint64_t view){
    ActorContext ctx;return viewContext(a,view,ctx)?1:0;
}
API uint32_t HBX_disable(Adapter*a){
    if(!a||a->magic!=ADAPTER_MAGIC)return 0;
    a->enabled=0;return 1;
}
API uint32_t HBX_route(Adapter*a,uint32_t site,HookFrame*f){
    if(!f||site>=SiteCount)return 0;
    if(site==Close){
        uint64_t view=f->r[CX];f->r[CX]=*reinterpret_cast<const uint64_t*>(view+0x128);
        if(a&&a->magic==ADAPTER_MAGIC){
            for(uint32_t i=0;i<8;i++)if(a->views[i].view==view){a->views[i].fresh=0;a->views[i].tiles=0;}
            for(uint32_t i=0;i<6;i++)a->caches[i].generation=0;
            ++a->calls[site];++a->routed[site];
        }
        return 1;
    }
    uint64_t continuation=f->next;
    if(site>=TabToggle&&site<=Name)f->next=f->reject; // exact native CALL fallback
    if(site==Purchase&&(!a||a->magic!=ADAPTER_MAGIC||!a->enabled)){
        f->next=f->reject;return 2; // installed confirmation hook must still cancel
    }
    if(!a||a->magic!=ADAPTER_MAGIC||!a->enabled)return 0;
    ++a->calls[site];ActorContext ctx{};bool ok=false;
    uint64_t board=0,slot=0;
    if(site>=TabToggle&&site<=Name){
        if(site==TabHeader)ok=menuContext(a,f->r[SI],ctx);
        else if(site==Name)ok=menuContext(a,*reinterpret_cast<const uint64_t*>(f->r[SP]+0x70),ctx);
        else ok=viewContext(a,f->r[DI],ctx);
        if(!ok||ctx.pair!=pairkey(0,4)){++a->rejected[site];return 0;}
        f->next=continuation;
        f->r[AX]=site==Name?reinterpret_cast<uint64_t>(a->paladin):0xff;
        ++a->routed[site];return 1;
    }
    if(site==BeginRefresh||site==EndRefresh||site==Purchase){
        uint64_t view=f->r[CX];
        ViewBinding*b=nullptr;
        for(uint32_t i=0;i<8;i++)if(a->views[i].view==view){b=&a->views[i];break;}
        if(site==BeginRefresh){
            bool previouslyPrivate=b!=nullptr;
            if(!b)for(uint32_t i=0;i<8;i++)if(!a->views[i].view){b=&a->views[i];break;}
            if(b)b->fresh=0;
            if(!b||!viewContext(a,view,ctx)){
                if(b){
                    if(previouslyPrivate)b->fresh=2; // block confirmation until native refresh ends
                    else b->view=0;
                }
                ++a->rejected[site];return 0;
            }
            b->view=view;b->menu=ctx.menu;b->keep=ctx.keep;b->actor=ctx.actor;b->pair=ctx.pair;
            b->generation=a->core.layouts[ctx.pair].generation;b->tiles=*reinterpret_cast<const uint64_t*>(view+0x120);
            for(uint32_t i=0;i<0x1c8;i++)b->snapshot[i]=byte(ctx.keep,i);
            ++a->routed[site];return 1;
        }
        if(site==EndRefresh&&b&&b->fresh==2){
            b->fresh=0;
            // Only release the private-view guard after a completed native
            // refresh of a stable unsupported context, never on a root failure.
            if(rootCurrent(a)&&!viewContext(a,view,ctx))b->view=0;
            ++a->rejected[site];return 0;
        }
        if(site==Purchase&&!b){
            // This view has not materialized hybrid tiles; preserve native behavior.
            f->r[CX]=dword(view,0x160);++a->rejected[site];return 0;
        }
        bool valid=b&&viewContext(a,view,ctx)&&b->menu==ctx.menu&&b->keep==ctx.keep&&b->pair==ctx.pair&&
            b->generation==a->core.layouts[ctx.pair].generation&&
            b->tiles==*reinterpret_cast<const uint64_t*>(view+0x120)&&
            equal(b->snapshot,reinterpret_cast<const uint8_t*>(ctx.keep),0x1c8);
        if(site==EndRefresh){
            if(b)b->fresh=valid?1:0;
            ++(valid?a->routed[site]:a->rejected[site]);return valid?1:0;
        }
        // Earliest accepted confirmation boundary, before any event writes.
        // Reject follows the verified native cancel branch using original RCX/RSP.
        uint32_t x=byte(view,0x550),y=byte(view,0x551);
        uint64_t tile=*reinterpret_cast<const uint64_t*>(view+0xc8);
        if(valid&&b->fresh==1&&f->r[DI]==view&&x<24&&y<24&&tile==b->tiles+(y*25+x)*0x38){
            uint32_t id=*reinterpret_cast<const uint16_t*>(tile+8);
            const Board&layout=a->core.layouts[ctx.pair].board;
            uint32_t flags=dword(tile,0x18);
            int32_t cost=*reinterpret_cast<const int16_t*>(tile+0xc);
            uint32_t liveCost=0;
            valid=id<=360&&layout.ids[y*24+x]==id&&!(byte(ctx.keep,0x194+id/8)&(1u<<(id%8)))&&
                !(flags&0x2000)&&(flags&0x1000)&&(flags&0x4000)&&cost>=0&&
                ownedNeighbor(layout,ctx.keep,x,y)&&
                currentCost(a,id,liveCost)&&uint32_t(cost)==liveCost&&dword(ctx.keep,0x190)>=liveCost&&
                reinterpret_cast<NativeGuard>(a->nativeGuard)(ctx.actor,id,0)==0;
        }else valid=false;
        if(!valid){if(b)b->fresh=0;f->next=f->reject;++a->rejected[site];return 2;}
        f->r[CX]=dword(view,0x160);b->fresh=0;++a->routed[site];return 1;
    }
    if(site==Preview){
        CacheBinding*match=nullptr;
        for(uint32_t i=0;i<6;i++){
            CacheBinding&c=a->caches[i];
            if(c.slot&&c.ordinal==uint32_t(f->r[SI])&&
               *reinterpret_cast<const uint64_t*>(c.slot)==f->r[BX]){
                if(match){match=nullptr;break;}match=&c;
            }
        }
        if(!match){++a->rejected[site];return 0;} // No private cache has owned this buffer.
        if(!match->board&&rootCurrent(a)&&!menuContext(a,match->menu,ctx)){
            ++a->rejected[site];return 0; // Fresh native cache for an unsupported pair.
        }
        if(match->board&&menuContext(a,match->menu,ctx)&&ctx.actor==match->actor&&ctx.pair==match->pair&&
           a->core.layouts[ctx.pair].generation==match->generation&&
           equal(match->snapshot,reinterpret_cast<const uint8_t*>(ctx.keep),0x1c8)){
            board=match->board;slot=reinterpret_cast<uint64_t>(&a->core.layouts[ctx.pair].pointer);ok=true;
        }else{
            // Zero dimensions suppress stale cache drawing, following native cleanup.
            f->r[AX]=reinterpret_cast<uint64_t>(&a->emptyPointer);f->r[CX]=a->emptyPointer;
            ++a->rejected[site];return 2;
        }
    }else if(site==Party){
        uint32_t ordinal=uint32_t(f->r[R12]);
        if(ordinal>=6){++a->rejected[site];return 0;}
        // Classify native as well as private caches. Otherwise the preview hook
        // would suppress every other job's normal board. Preserve known private
        // classifications across invalidation so stale caches cannot fall back.
        CacheBinding&cache=a->caches[ordinal];
        cache.slot=f->r[BP];cache.menu=f->r[R13];cache.ordinal=ordinal;
        cache.board=0;cache.generation=0;
        ok=menuContext(a,f->r[R13],ctx);
    }
    else if(site==Refund){ok=keepContext(a,f->r[R14],ctx);}
    else {
        uint64_t view=site==Geometry?f->r[DI]:site==Extent?f->r[SI]:
            (site==Find||site==FindSelected)?f->r[BX]:f->r[BP];
        ok=viewContext(a,view,ctx);
        uint64_t expected=site==Refresh?f->r[R13]:
            (site==Extent||site==Remembered||site==Fallback)?f->r[R15]:0;
        if(ok&&expected&&expected!=ctx.menu)ok=false;
    }
    if(!ok){++a->rejected[site];return 0;}
    Layout&l=a->core.layouts[ctx.pair];
    if(!board){board=reinterpret_cast<uint64_t>(&l.board);slot=reinterpret_cast<uint64_t>(&l.pointer);}
    switch(site){
        case Geometry:f->r[AX]=board;f->r[CX]=slot;break;
        case Refresh:case Extent:f->r[DX]=board;break;
        case Remembered:f->r[R9]=board;break;
        case Fallback:f->r[CX]=board;break;
        case Party:{
            uint32_t ordinal=uint32_t(f->r[R12]);if(ordinal>=6){++a->rejected[site];return 0;}
            CacheBinding&cache=a->caches[ordinal];
            cache.slot=f->r[BP];cache.menu=ctx.menu;cache.keep=ctx.keep;
            cache.board=board;cache.actor=ctx.actor;cache.pair=ctx.pair;
            cache.generation=l.generation;cache.ordinal=ordinal;
            for(uint32_t i=0;i<0x1c8;i++)cache.snapshot[i]=byte(ctx.keep,i);
            f->r[R15]=slot;f->r[CX]=board;break;
        }
        case Preview:f->r[AX]=slot;f->r[CX]=board;break;
        case Find:f->r[R9]=board;f->r[R10]=l.board.width;break;
        case FindSelected:f->r[R9]=board;f->r[DX]=l.board.width;break;
        case Refund:
            if(!refundUnion(a,ctx)){++a->rejected[site];return 0;}
            f->r[R8]=a->refundPointers[ctx.actor];
            f->r[R12]=reinterpret_cast<uint64_t>(&a->refundPointers[ctx.actor]);break;
        default:return 0;
    }
    ++a->routed[site];return 1;
}
