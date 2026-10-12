"""标的引用对象：``stockref`` / ``refobj`` / ``refquote`` / ``externallink``。

为什么单独一档：

* 这四个模块是一组**互相引用**的对象模型（``refobj`` 里的类继承自
  ``stockref``，``refquote`` 给对象灌行情，``externallink`` 给对象生成外链），
  对应 PHP 的 ``php/stock/stockref.php`` + ``php/stock/ref*.php``
  + ``php/externallink.php``，拆开会把一个继承链散到几个目录；
* 它们体量最大（``refobj.py`` 五万字符），单独一档后根目录的
  "大文件压迫感"消失；
* ``ref`` 允许反向依赖 ``sql``（读历史净值）与 ``calc``，但**不允许**
  ``sql`` 之外的层反过来依赖它的私有实现——对外只暴露
  ``build_qdii_reference()`` 这一族工厂函数。
"""

