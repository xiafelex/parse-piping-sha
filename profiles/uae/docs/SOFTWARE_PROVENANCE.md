# 软件来源与公开资料检索（2026-10-02）

## 本地直接证据

四份主SHA的AppObject均可提取Smart3D/Common2D/Rad2D安装路径，示例：
`C:\Program Files (x86)\Smart3D\Common2D\Rad2D\bin\...`。
DocVersion3记录包含`shape2dserver.a`、`SmartSketch.app`，嵌入对象也有这些版本信息。
其中可见090400.0064、090400.0073、090400.0086等2D模块版本；不能把它们直接当成Smart 3D产品版本。
TaggedTxtData/TitleArea还保存HAB_COMPLEX及当前图号，TaggedTxtData/Revision保存历史版本和签审字段。

由直接路径及官方发布格式，可高置信度归属为Intergraph/Hexagon Smart 3D（S3D/SP3D）生态，Isogen出图，SmartSketch/Shape2D承担二维文档。
不能仅凭PCF扩展名识别设计平台：多家设计软件都调用Isogen。
文件中旧DGN路径属于嵌入/粘贴图形来源，不能据此改判整套工程来自MicroStation。
PDF的Ghostscript、Microsoft Print to PDF、Bluebeam信息只证明打印或后处理工具，不是建模平台证据。

## 官方资料与本地证据的对应

1. [Smart 3D：修订与发布](https://docs.hexagonppm.com/r/en-US/Intergraph-Smart-3D-Isogen-Isometric-Drawings/13.1/1133049)
   官方说明RAD/SHA是发布图形，可随附PCF/POD，并可用SmartSketch查看。支持本批套件关系判断。
2. [Integration：可发布文档](https://docs.hexagonppm.com/r/en-US/SmartPlant-Integration-COM-Help/567243)
   Smart 3D轴测图发布项中明确列出SHA及配套PCF、POD、中性文件和TXT报表。与本目录组合一致。
3. [Isogen：创建定制报表](https://docs.hexagonppm.com/r/en-US/ISOGEN-Configuration-Help/Version-15/895440)
   描述出图中间POD的图形/文字，以及从对象属性生成报表的机制。POD并非只有工程三维数据。
4. [Spoolgen：访问焊口坐标](https://docs.hexagonppm.com/r/en-US/Intergraph-Spoolgen-Help/Version-2019-9.0/849718)
   官方给出CKP[Run_1].X等关键点访问方式。与已解出的Run_1/XYZ结构一致，但不提供其二进制字段布局。
5. [Smart Interop Publisher：转换Isogen文件](https://docs.hexagonppm.com/r/en-US/Intergraph-Smart-Interop-Publisher-Help/Version-17/1196782)
   将SHA/IGR、ToolData/MetaData XML与PCF/POD作为配套输入。需要同名、同目录关联；本项目导出后带-pcf/-pod后缀，本地程序已显式配对。
6. [Smart Isometrics：XMpLant导出](https://docs.hexagonppm.com/r/en-US/Intergraph-Smart-Isometrics-Help/Version-7.2/299747)
   厂商可将POD工程数据映射导出XML。它适合后续验证对象语义，不等于提供完整图形二进制定义。
7. [Smart 3D 12：Isogen变更说明](https://docs.hexagonppm.com/r/en-US/Intergraph-Smart-3D-Isogen-Isometric-Drawings/12/905827)
   说明较早外置的POD Graphics Processor后来集成到Isogen Reporting。不同年代文档对是否随产品提供有差异，不能混用版本结论。

## 官网社区和论坛检索

检索覆盖Hexagon官方文档/社区索引、Eng-Tips及Isogen/POD相关论坛关键词。
没有找到可直接采用的、公开完整的POD二进制序列化规范；这不是断言厂商内部没有规范。
检索也命中第三方CADWorx/POD错误讨论，但不能用别人的项目日志证明本项目来源，因此未将其作为格式定义。

可公开读取且由软件厂商维护的社区参考：
[Bentley官方社区：AutoISOGEN错误码](https://bentleysystems.service-now.com/community?id=kb_article&sysparm_article=KB0102085)。
其中区分POD生成阶段与PODGRAPHICS输出阶段，说明Isogen跨多个宿主产品使用。
该社区资料仅辅助确认处理阶段，不支持猜测本批对象布局。

## 对继续解码的帮助

官方文档提供概念与导出路径；二进制边界、类型、坐标单位仍以本地四样本验证为依据。
最有效的下一组验证资料是：同一POD从厂商导出的XMpLant XML或对象属性报表、对应版本POD schema/SDK定义、含弧线和文字排版变化的受控样本。
现有资料足以建立无损树、构件关键点、属性和主要图元对应；不足以将“完整语义及视觉还原”标记为完成。
