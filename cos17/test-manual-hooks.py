"""Compile and execute the actual manual-hook call sites with narrow C stubs.

Run from the kernel root. No Android runtime or phone is needed.
"""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile


def function(text, marker):
    start = text.index(marker)
    opening = text.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


def program():
    exe = Path('fs/exec.c').read_text()
    rw = Path('fs/read_write.c').read_text()
    stat = Path('fs/stat.c').read_text()
    if exe.count('ksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);') != 1:
        raise ValueError('exec hook must receive valid locals exactly once in the common path')
    if 'ksu_handle_execveat((int *)AT_FDCWD' in exe or 'ksu_handle_vfs_read(' in rw:
        raise ValueError('obsolete or duplicate manual hook remains')
    if 'extern void ksu_handle_sys_read(unsigned int fd);' not in rw:
        raise ValueError('read hook does not match the pinned KernelSU signature')
    functions = '\n'.join(function(exe, marker) for marker in (
        'static int do_execveat_common(', 'int do_execve_file(',
        'int do_execve(', 'int do_execveat('))
    functions += '\n#ifdef CONFIG_COMPAT\n' + '\n'.join(
        function(exe, marker) for marker in (
            'static int compat_do_execve(', 'static int compat_do_execveat('))
    functions += '\n#endif\n'
    functions += function(rw, 'SYSCALL_DEFINE3(read,')
    functions += function(stat, 'SYSCALL_DEFINE2(newfstat,')
    functions += function(stat, 'SYSCALL_DEFINE2(fstat64,')
    return HEADER + functions + MAIN


HEADER = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#define __user
#define AT_FDCWD (-100)
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p) >= (uintptr_t)-4095)
#define READ_ONCE(x) (x)
#define unlikely(x) (x)
#define SYSCALL_DEFINE2(n,t1,a1,t2,a2) static long sys_##n(t1 a1,t2 a2)
#define SYSCALL_DEFINE3(n,t1,a1,t2,a2,t3,a3) static long sys_##n(t1 a1,t2 a2,t3 a3)
typedef uint32_t compat_uptr_t;
struct filename { const char *name; } original_name = {"original"}, replacement = {"replacement"};
struct file { int unused; };
struct user_arg_ptr {
#ifdef CONFIG_COMPAT
    bool is_compat;
#endif
    union { const char *const *native; const compat_uptr_t *compat; } ptr;
};
struct stat { long st_size; };
struct stat64 { long long st_size; };
struct kstat { int unused; };
static int hooks, common_calls, stat_hooks, copies, read_hooks, reads;
static int expected_fd, expected_flags, expected_compat, stat_error, copy_error;
static bool ksu_init_rc_hook;
static const char *const *expected_argv;
static const char *const *expected_envp;
static int ksu_handle_execveat(int *fd, struct filename **name, void *a, void *e, int *flags)
{
    struct user_arg_ptr *argv = a, *envp = e;
    assert(fd && flags && name && argv && envp);
    assert(*fd == expected_fd && *flags == expected_flags && *name == &original_name);
#ifdef CONFIG_COMPAT
    assert(argv->is_compat == expected_compat && envp->is_compat == expected_compat);
#endif
    assert((const void *)argv->ptr.native == (const void *)expected_argv);
    assert((const void *)envp->ptr.native == (const void *)expected_envp);
    ++hooks;
    *fd = 41; *flags = 43; *name = &replacement;
    return 0;
}
static int __do_execve_file(int fd, struct filename *name, struct user_arg_ptr argv,
                           struct user_arg_ptr envp, int flags, struct file *file)
{
    (void)argv; (void)envp; (void)file;
    ++common_calls;
#ifdef CONFIG_KSU_MANUAL_HOOK
    if (!IS_ERR_OR_NULL(name))
        assert(fd == 41 && flags == 43 && name == &replacement);
#else
    if (!IS_ERR_OR_NULL(name))
        assert(fd == expected_fd && flags == expected_flags && name == &original_name);
#endif
    return 17;
}
static void ksu_handle_sys_read(unsigned int fd) { assert(fd == 7); ++read_hooks; }
static long ksys_read(unsigned int fd, char *buf, size_t count)
{ (void)buf; assert(fd == 7 && count == 9); ++reads; return 9; }
static int vfs_fstat(unsigned long fd, struct kstat *st)
{ (void)st; assert(fd == 7); return stat_error; }
static int cp_new_stat(struct kstat *st, struct stat *out)
{ (void)st; ++copies; if (!copy_error) out->st_size = 100; return copy_error; }
static int cp_new_stat64(struct kstat *st, struct stat64 *out)
{ (void)st; ++copies; if (!copy_error) out->st_size = 100; return copy_error; }
static void ksu_handle_newfstat_ret(unsigned int *fd, struct stat **st)
{ assert(*fd == 7 && (*st)->st_size == 100); ++stat_hooks; (*st)->st_size += 60; }
static void ksu_handle_fstat64_ret(unsigned long *fd, struct stat64 **st)
{ assert(*fd == 7 && (*st)->st_size == 100); ++stat_hooks; (*st)->st_size += 60; }
'''

MAIN = r'''
int main(void)
{
    const char *argv[] = {"init", "second_stage", NULL}, *envp[] = {NULL};
    struct stat st = {0}; struct stat64 st64 = {0};
    int expected_hooks = 0;
    expected_argv = argv; expected_envp = envp;
    expected_fd = AT_FDCWD; expected_flags = 0;
    assert(do_execve(&original_name, argv, envp) == 17); ++expected_hooks;
    expected_fd = 29; expected_flags = 0x1000;
    assert(do_execveat(29, &original_name, argv, envp, 0x1000) == 17); ++expected_hooks;
#ifdef CONFIG_COMPAT
    {
        compat_uptr_t ca[] = {1, 2, 0}, ce[] = {0};
        expected_argv = (const char *const *)ca; expected_envp = (const char *const *)ce;
        expected_compat = 1; expected_fd = AT_FDCWD; expected_flags = 0;
        assert(compat_do_execve(&original_name, ca, ce) == 17); ++expected_hooks;
        expected_fd = 31; expected_flags = 0x1000;
        assert(compat_do_execveat(31, &original_name, ca, ce, 0x1000) == 17); ++expected_hooks;
    }
#endif
    assert(common_calls == expected_hooks);
#ifdef CONFIG_KSU_MANUAL_HOOK
    assert(hooks == expected_hooks);
#else
    assert(hooks == 0);
#endif
    {
        int before = hooks;
        assert(do_execve((struct filename *)(intptr_t)-14, argv, envp) == 17);
        assert(do_execveat(0, NULL, argv, envp, 0) == 17);
        assert(do_execve_file(NULL, argv, envp) == 17);
        assert(hooks == before);
    }
    ksu_init_rc_hook = true;
    assert(sys_read(7, NULL, 9) == 9);
    ksu_init_rc_hook = false;
    assert(sys_read(7, NULL, 9) == 9 && reads == 2);
    ksu_init_rc_hook = true;
    assert(sys_newfstat(7, &st) == 0 && sys_fstat64(7, &st64) == 0);
#ifdef CONFIG_KSU_MANUAL_HOOK
    assert(stat_hooks == 2 && st.st_size == 160 && st64.st_size == 160 && read_hooks == 1);
#else
    assert(stat_hooks == 0 && st.st_size == 100 && st64.st_size == 100 && read_hooks == 0);
#endif
    {
        int before = stat_hooks;
        stat_error = -9;
        assert(sys_newfstat(7, &st) == -9 && sys_fstat64(7, &st64) == -9 && copies == 2);
        stat_error = 0; copy_error = -14;
        assert(sys_newfstat(7, &st) == -14 && sys_fstat64(7, &st64) == -14 && copies == 4);
        assert(stat_hooks == before);
        copy_error = 0; ksu_init_rc_hook = false;
        assert(sys_newfstat(7, &st) == 0 && sys_fstat64(7, &st64) == 0);
        assert(stat_hooks == before && st.st_size == 100 && st64.st_size == 100);
    }
    puts("PASS: execve/execveat, native/compat, hook arguments, invalid paths, init read and fstat failures");
    return 0;
}
'''


if __name__ == '__main__':
    code = program()
    with tempfile.TemporaryDirectory(prefix='ksu-manual-hooks-') as directory:
        src = Path(directory) / 'hooks.c'
        src.write_text(code)
        for manual in (False, True):
            for compat in (False, True):
                binary = Path(directory) / ('hooks.exe' if os.name == 'nt' else 'hooks')
                defines = (['-DCONFIG_KSU_MANUAL_HOOK'] if manual else [])
                defines += ['-DCONFIG_COMPAT'] if compat else []
                subprocess.run(shlex.split(os.environ.get('CC', 'gcc')) + [
                    '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-Wno-unused-variable', '-O2', *defines, str(src), '-o', str(binary)], check=True)
                subprocess.run([str(binary)], check=True)
