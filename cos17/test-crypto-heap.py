#!/usr/bin/env python3
"""Compile the actual reserved-memory callback and check r8s-only heap policy."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HEADER = r"""
#undef NDEBUG
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define __init
#define PAGE_SIZE 4096
#define PAGE_SHIFT 12
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define IS_ENABLED(x) (x)
#define CONFIG_ION_RBIN_HEAP 1
#define EINVAL 22
#define ENOMEM 12
#define pr_info(...) ((void)0)
#define perrfn(...) ((void)0)
#define kmemleak_ignore_phys(...) ((void)0)
#define be32_to_cpu(x) (x)
typedef uint32_t __be32;
typedef uint32_t __u32;
typedef uint64_t phys_addr_t;
struct cma { int unused; } cma;
struct reserved_mem { int fdt_node; bool reusable; const char *name; phys_addr_t base,size; };
struct record { char *heapname; struct cma *cma; phys_addr_t base,size;
 unsigned int alloc_align,protection_id; bool secure,untouchable,recyclable; };
static struct record ion_reserved_mem[16];
static unsigned int reserved_mem_count;
static const char *heap_name;
static bool reuse_flag,recycle_flag,secure_flag;
static unsigned int cma_calls;
static const void *of_get_flat_dt_prop(int node,const char *name,int *len) {
 static uint32_t present=0, alignment=65536;
 (void)node; if(len)*len=0;
 if(!strcmp(name,"ion,heapname"))return heap_name;
 if(!strcmp(name,"ion,reusable"))return reuse_flag?&present:NULL;
 if(!strcmp(name,"ion,recyclable"))return recycle_flag?&present:NULL;
 if(!strcmp(name,"ion,secure"))return secure_flag?&present:NULL;
 if(!strcmp(name,"ion,alignment")) { if(len)*len=4; return &alignment; }
 return NULL;
}
static int need_ion_rbin_heap(void) { return 1; }
static unsigned int get_order(unsigned long size) {
 unsigned int order=0; while((4096UL<<order)<size)order++;return order;
}
static int exynos_cma_init_reserved_mem(phys_addr_t base,phys_addr_t size,
 int bit,const char *name,struct cma **result) {
 assert(base==0xee800000 && size==25165824 && bit==0 && !strcmp(name,heap_name));
 cma_calls++;*result=&cma;return 0;
}
"""
TRAILER = r"""
static void check(const char *name,bool reuse,bool recycle,bool secure) {
 struct reserved_mem memory={.name="test",.base=0xee800000,.size=25165824};
 bool dedicated=CONFIG_MODEL_R8S && !strcmp(name,"crypto_heap");
 heap_name=name;reuse_flag=reuse;recycle_flag=recycle;secure_flag=secure;
 reserved_mem_count=cma_calls=0;memset(ion_reserved_mem,0,sizeof(ion_reserved_mem));
 assert(exynos_ion_reserved_mem_setup(&memory)==0);
 assert(reserved_mem_count==1);
 assert(memory.reusable==(dedicated?false:(reuse||recycle)));
 assert(cma_calls==(unsigned int)(!dedicated&&reuse));
 assert(!!ion_reserved_mem[0].cma==(!dedicated&&reuse));
 assert(ion_reserved_mem[0].recyclable==(!dedicated&&recycle));
 assert(ion_reserved_mem[0].secure==secure);
 assert(ion_reserved_mem[0].base==memory.base && ion_reserved_mem[0].size==memory.size);
 assert(ion_reserved_mem[0].alloc_align==65536);
}
int main(void) {
 const char *names[]={"crypto_heap","vstream_heap","camera_heap","npu_fw"};
 for(unsigned int i=0;i<4;i++)for(int r=0;r<2;r++)for(int c=0;c<2;c++)
  for(int s=0;s<2;s++)check(names[i],r,c,s);
 puts("64 heap policy cases passed");return 0;
}
"""

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc',default='cc')
    args=parser.parse_args()
    text=(ROOT/'drivers/staging/android/ion/ion_fdt_exynos.c').read_text()
    start=text.index('static int __init exynos_ion_reserved_mem_setup(')
    body=text.index('{',start);depth=1;end=body+1
    while depth:
        depth += (text[end]=='{')-(text[end]=='}');end+=1
    callback=text[start:end]
    compiler=[args.cc]+(['cc'] if Path(args.cc).stem=='zig' else [])
    with tempfile.TemporaryDirectory(prefix='r8s-crypto-policy-') as directory:
        directory=Path(directory);source=directory/'test.c'
        source.write_text(HEADER+callback+TRAILER)
        for r8s in (0,1):
            output=directory/('check-'+str(r8s)+'.exe')
            subprocess.run(compiler+['-std=c11','-O2','-Wall','-Wextra','-Werror',
                           '-DCONFIG_MODEL_R8S='+str(r8s),str(source),'-o',str(output)],check=True)
            subprocess.run([str(output)],check=True)
    print('r8s dedicated crypto pool and unchanged other-model policy verified')

if __name__=='__main__':main()
