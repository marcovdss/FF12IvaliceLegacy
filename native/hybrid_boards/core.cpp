// Offline C ABI candidate. No process APIs, hooks, allocation, logging or I/O.
// All pointer arguments must refer to validated readable/writable owned ranges.
// Compile as an import-free Windows x64 DLL; not an EFL/plugin entry point.
#include <stdint.h>
#define API extern "C" __declspec(dllexport)
constexpr uint32_t MAGIC=0x48424F31, EMPTY=0xffff, MAXV=4;
struct Board { uint32_t tag; uint16_t width,height; uint16_t ids[576]; };
struct Layout { Board board; uint32_t generation; uint64_t pointer; };
struct Binding { uint64_t view,keep,menu; uint32_t handle,pair,generation,epoch;
    uint8_t actor,j1,j2,selected,slot,fresh; uint16_t width,height;
    uint16_t ids[625]; };
struct State { uint32_t magic,epoch,objectCount,reserved;
    uint64_t resolver,keepRoot,rootSlot,rootValue;
    Layout layouts[66]; Binding views[MAXV]; };
struct Request { uint64_t view,resolver,keepRoot,rootSlot; uint32_t objectCount,reserved; };
struct Input { uint32_t j1,j2,width,height; uint16_t ids[576]; };
struct Point { int32_t x,y; uint32_t actualID; uint64_t actualMenu; };
using Resolver=uint64_t(*)(int32_t);
static uint8_t byte(uint64_t p,uint32_t n){return reinterpret_cast<const uint8_t*>(p)[n];}
static uint32_t dword(uint64_t p,uint32_t n){return *reinterpret_cast<const uint32_t*>(p+n);}
static uint32_t pairkey(uint32_t a,uint32_t b){
    if(a>11||b>11||a==b)return 66;
    if(a>b){uint32_t t=a;a=b;b=t;} return a*(23-a)/2+b-a-1;
}
static Binding* find(State*s,uint64_t view){
    if(!s||s->magic!=MAGIC||!view)return nullptr;
    for(uint32_t i=0;i<MAXV;i++)if(s->views[i].view==view)return &s->views[i];
    return nullptr;
}
static bool current(State*s,Binding*b){
    if(!b||b->pair>=66||!b->keep)return false;
    // Validate the configured object-pool snapshot before calling its unbounded
    // native resolver. A supervisor must reconfigure on pool-count/lifecycle changes.
    bool ok=s->objectCount&&b->epoch==s->epoch&&b->handle<s->objectCount&&
        *reinterpret_cast<const uint64_t*>(s->rootSlot)==s->rootValue;
    if(ok){uint64_t menu=reinterpret_cast<Resolver>(s->resolver)(int32_t(b->handle));
        ok=menu&&menu==b->menu&&byte(menu,0x60)==b->actor;}
    ok=ok&&byte(b->keep,4)==b->actor&&byte(b->keep,5)==0&&
        byte(b->keep,0x1c3)==b->j1&&byte(b->keep,0x1c4)==b->j2&&
        byte(b->keep,0x1c5)==b->selected&&dword(b->view,0x160)==b->handle&&
        byte(b->view,0x558)==b->slot&&s->layouts[b->pair].generation==b->generation;
    if(!ok)b->fresh=0;
    return ok;
}
API uint32_t HB_size(){return sizeof(State);}
API uint32_t HB_init(State*s,uint32_t size){
    if(!s||size!=sizeof(State))return 0;
    for(uint32_t i=0;i<sizeof(State);i++)reinterpret_cast<volatile uint8_t*>(s)[i]=0;
    s->magic=MAGIC;return 1;
}
API uint32_t HB_config(State*s,const Request*r){
    if(!s||s->magic!=MAGIC)return 0;
    s->objectCount=0;
    for(uint32_t i=0;i<MAXV;i++)s->views[i].fresh=0;
    if(s->epoch==0xffffffff)return 0;
    ++s->epoch;
    if(!r||!r->resolver||!r->rootSlot||!r->keepRoot||!r->objectCount)return 0;
    uint64_t root=*reinterpret_cast<const uint64_t*>(r->rootSlot);
    if(!root)return 0;
    s->resolver=r->resolver;s->rootSlot=r->rootSlot;s->rootValue=root;
    s->keepRoot=r->keepRoot;s->objectCount=r->objectCount;return 1;
}
API uint32_t HB_put(State*s,const Input*in){
    if(!s||s->magic!=MAGIC||!in)return 0;
    uint32_t p=pairkey(in->j1,in->j2);
    if(p>=66||!in->width||!in->height||in->width>24||in->height>24)return 0;
    uint32_t count=in->width*in->height;
    for(uint32_t i=0;i<count;i++)if(in->ids[i]>360&&in->ids[i]!=EMPTY)return 0;
    Layout&l=s->layouts[p];
    if(l.generation==0xffffffff)return 0; // refuse generation wrap
    l.board.tag=0x6463696c; l.board.width=uint16_t(in->width);l.board.height=uint16_t(in->height);
    for(uint32_t i=0;i<576;i++)l.board.ids[i]=i<count?in->ids[i]:uint16_t(EMPTY);
    ++l.generation;
    l.pointer=reinterpret_cast<uint64_t>(&l.board);
    for(uint32_t i=0;i<MAXV;i++)if(s->views[i].pair==p)s->views[i].fresh=0;
    return l.generation;
}
API uint64_t HB_bind(State*s,const Request*r){
    if(!s||s->magic!=MAGIC||!r||!r->view)return 0;
    Binding*b=find(s,r->view);
    if(b){b->fresh=0;b->keep=0;} // failed rebind must invalidate prior view
    if(!s->objectCount||r->resolver!=s->resolver||r->keepRoot!=s->keepRoot||
       r->rootSlot!=s->rootSlot||r->objectCount!=s->objectCount||
       *reinterpret_cast<const uint64_t*>(s->rootSlot)!=s->rootValue)return 0;
    int32_t handle=int32_t(dword(r->view,0x160));
    if(handle<0||uint32_t(handle)>=r->objectCount)return 0;
    uint64_t menu=reinterpret_cast<Resolver>(r->resolver)(handle);
    if(!menu)return 0;
    uint8_t actor=byte(menu,0x60);
    if(actor>39)return 0;
    uint64_t keep=r->keepRoot+8+uint64_t(actor)*0x1c8;
    uint8_t j1=byte(keep,0x1c3),j2=byte(keep,0x1c4),selected=byte(keep,0x1c5),slot=byte(r->view,0x558);
    uint32_t p=pairkey(j1,j2);
    if(byte(keep,4)!=actor||actor>6||byte(keep,5)||selected>11||slot>11||
       p>=66||!s->layouts[p].generation)return 0;
    if(!b)for(uint32_t i=0;i<MAXV;i++)if(!s->views[i].view){b=&s->views[i];break;}
    if(!b)return 0;
    b->view=r->view;b->keep=keep;b->menu=menu;b->epoch=s->epoch;b->handle=uint32_t(handle);b->pair=p;
    b->generation=s->layouts[p].generation;b->actor=actor;b->j1=j1;b->j2=j2;
    b->selected=selected;b->slot=slot;b->fresh=0;
    return reinterpret_cast<uint64_t>(&s->layouts[p].board);
}
API uint64_t HB_slot(State*s,uint64_t view){
    Binding*b=find(s,view);if(!current(s,b))return 0;
    return reinterpret_cast<uint64_t>(&s->layouts[b->pair].pointer);
}
API uint32_t HB_refresh(State*s,uint64_t view){
    Binding*b=find(s,view);if(!current(s,b))return 0;
    const Board&board=s->layouts[b->pair].board;
    b->width=board.width+1;b->height=board.height+1;
    for(uint32_t i=0;i<625;i++)b->ids[i]=uint16_t(EMPTY);
    for(uint32_t y=0;y<board.height;y++)for(uint32_t x=0;x<board.width;x++)
        b->ids[y*b->width+x]=board.ids[y*board.width+x];
    b->fresh=1;return uint32_t(b->width)|(uint32_t(b->height)<<16);
}
API uint32_t HB_cell(State*s,uint64_t view,int32_t x,int32_t y){
    Binding*b=find(s,view);if(!current(s,b)||!b->fresh||x<0||y<0||x>=b->width||y>=b->height)return EMPTY;
    return b->ids[uint32_t(y)*b->width+uint32_t(x)];
}
API uint32_t HB_guard(State*s,uint64_t view,const Point*p){
    // ID coherence only. Native adjacency, live LP affordability, ownership,
    // restriction/Esper/Quickening eligibility must be checked by the full UI.
    if(!p||p->actualID==EMPTY||p->actualID>360)return EMPTY;
    Binding*b=find(s,view);
    if(!b||p->actualMenu!=b->menu)return EMPTY;
    uint32_t id=HB_cell(s,view,p->x,p->y);
    return id==p->actualID?id:EMPTY;
}
API uint32_t HB_cursor(State*s,uint64_t view,uint32_t license){
    Binding*b=find(s,view);if(!current(s,b)||!b->fresh||license==EMPTY)return 0xffffffff;
    for(uint32_t y=0;y<b->height;y++)for(uint32_t x=0;x<b->width;x++)
        if(b->ids[y*b->width+x]==license)return x|(y<<16);
    return 0xffffffff;
}
API uint32_t HB_release(State*s,uint64_t view){
    Binding*b=find(s,view);if(!b)return 0;
    b->fresh=0;b->view=0;b->keep=0;return 1;
}
