# 代码整合记录（2026-09-29）

## 正式维护位置

`parse-piping-sha` 是唯一日常代码仓库：

| 路径 | 用途 |
| --- | --- |
| profiles/lanxing/parser | 原蓝星解析和绘制实现 |
| profiles/lanxing/workflow | 蓝星批量转换与成果整理 |
| profiles/chengda_indonesia/parser | 成达解析、拓扑匹配、联合布局 |
| profiles/chengda_indonesia/workflow | 表格读取、批处理、审计、PCF反写 |
| profiles/chengda_indonesia/units | 14个既有单元专用入口 |
| shared | 原工作区根目录中的两个共用PCF工具 |
| tools/chengda | 分析、诊断、预检和验证脚本 |

没有专用入口的单元继续使用成达通用入口及显式路径参数，不臆造新单元策略。
代码根目录旧蓝星命令和工作区根目录旧批处理命令保留轻量转发，正式逻辑只有一份。

成达输入、输出、单元签名仍在原项目目录；通用签名及专用字体在
`成达印尼项目/本地资源/parser-assets`。环境变量 `CHENGDA_PROJECT_DIR`、
`CHENGDA_ASSET_DIR` 可覆盖数据与资源路径。新机器还需要同等系统字体与浏览器环境。
与SHA无关的PCF处理脚本及材料汇总脚本不属于本次迁移范围。

## 验证证据

- 14个既有单元入口迁移前后 `--plan-only` 输出一致，均正常退出。
- 000201、400101、201600各一页：迁移前后6次完整水印编号转换成功，PDF按2倍分辨率逐像素一致。
- 蓝星701样本：迁移前后SVG的SHA256完全一致。
- 历史测试：迁移前120通过、51失败；迁移后128通过、51失败，无新增失败。
  新增8项来自旧分支的独有拓扑测试；其中两项模拟图形的测试补齐视口桩以适配现有实现。
  历史失败包括已失效的本地图纸路径和旧断言，不宣称整个测试集全绿。
- 旧目录归档后，PCF反写、拓扑恢复及图号编码15项测试通过。
- 旧目录归档后，通过 `run_chengda.py` 再生成000201样本成功：10个焊口、11个管理编号，PDF逐像素仍与迁移前一致。
- 迁移快照全部文件SHA256复核通过；成达正式模块未遗漏文件或顶层函数/类。
  函数内部差异仅有6处签名、字体及资源准备路径替换。

详细结果：`.local/relocation-verification/`。正式PDF和审计未被迁移测试覆盖。

## 备份和旧分支

- `.local/migration/20260929-155615/`：迁移前代码快照与逐文件SHA256清单。
- `.local/archive-20260929/`：原成达两个代码目录及旧分支worktree的可恢复归档。
- 旧分支仍注册为worktree，未提交改动和分支历史保留；不再用于正式运行。
  如后续移除必须使用Git worktree管理命令，不能直接删除Git内部目录。
- `.local/`已忽略，不进入Git上传。

主工作区原有的research、tests删除状态及未提交修改未被重置。
桌面打包路径已补充新profile目录，本轮没有重新构建桌面安装包。
此次整合未提交或推送Git，上传前应逐项暂存本次代码和说明，核对原有工作区变更。
不上传生产SHA、PCF、Excel、PDF、签名及参考图提取字体。

## 常用命令

从仓库根目录运行：

```powershell
python run_chengda.py --list-units
python run_chengda.py --unit 000201 --plan-only
python run_chengda.py --unit 000201 --workers 8 --force
```

统一入口默认8个工作进程，保留原单元策略和所有原转换参数。
