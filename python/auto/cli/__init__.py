"""命令行入口：``python -B python\\auto\\cli\\fundcli.py ...`` 这样调用。

为什么单独一档：

* 每个文件都是**可执行脚本**（有 ``main()`` 与 ``if __name__ == "__main__"``），
  与"被 import 的模块"职责不同，分开后一眼能看出哪些是入口；
* 这些脚本头部都要先把 ``python/auto`` 与 ``python`` 塞进 ``sys.path``
  才能 import 同目录的平铺模块（见各文件里的 ``AUTO_DIR`` 推导），
  挪进子目录后这段推导多了一层 ``dirname``，是本档唯一需要留意的差异；
* 对应 PHP 的 ``php/test/*.php`` 与 ``php/ui/submit*.php`` 里的命令行流程。
"""

