"""Do not echo untrusted command arguments, which may contain mistyped secrets."""
import argparse
import sys


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f'{self.prog}: 参数格式无效，请使用 --help 查看用法；秘密请通过隐藏输入提供\n')
