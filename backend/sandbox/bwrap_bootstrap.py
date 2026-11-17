"""隔离内可信引导：先装不可撤销的单进程系统调用策略，再运行审核脚本。"""
import ctypes
import errno
import os
from pathlib import Path
import resource
import sys


def main():
    code = Path('/main.py').read_bytes()
    library = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    # 所有输入只读，禁止创建进程/线程、执行程序、联网、提权和修改限额。
    context = library.seccomp_init(0x00050000 | errno.EPERM)
    if not context:
        raise RuntimeError('seccomp初始化失败')
    allowed = ('read write close fstat newfstatat stat statx lstat lseek pread64 open openat '
               'readlink readlinkat getdents64 access faccessat faccessat2 '
               'mmap mprotect munmap mremap brk madvise futex '
               'rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack '
               'getpid gettid getppid getuid geteuid getgid getegid '
               'clock_gettime clock_getres gettimeofday time nanosleep clock_nanosleep '
               'getrandom uname getcwd fcntl exit exit_group sched_getaffinity').split()
    try:
        for name in allowed:
            number = library.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and library.seccomp_rule_add(context, 0x7fff0000, number, 0) != 0:
                raise RuntimeError('seccomp规则安装失败')
        # Python 3.8的文件打开会设置close-on-exec并查询终端；不放行其他ioctl。
        class Comparison(ctypes.Structure):
            _fields_=[('arg',ctypes.c_uint),('op',ctypes.c_uint),('a',ctypes.c_uint64),('b',ctypes.c_uint64)]
        for request in (0x5451,0x5401):
            number=library.seccomp_syscall_resolve_name(b'ioctl')
            if number<0 or library.seccomp_rule_add(context,0x7fff0000,number,1,Comparison(1,4,request,0))!=0:
                raise RuntimeError('只读文件兼容规则安装失败')
        resource.setrlimit(resource.RLIMIT_AS, (256*1024*1024,)*2)
        resource.setrlimit(resource.RLIMIT_CPU, (45,45))
        resource.setrlimit(resource.RLIMIT_CORE, (0,0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0,0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32,32))
        resource.setrlimit(resource.RLIMIT_NPROC, (1,1))
        if library.seccomp_load(context) != 0:
            raise RuntimeError('seccomp加载失败')
    finally:
        library.seccomp_release(context)
    exec(compile(code, '/main.py', 'exec'), {'__name__':'__main__','__file__':'/main.py'})


if __name__ == '__main__':
    main()
