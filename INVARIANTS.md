# 表示不变量说明（SDK2 目标存储）

本文件说明升级后归档的表示不变量。迁移与兼容代码不调用 SDK1 的任何概率
计算；SDK1 仅用于旧 API 值对象的类型兼容、构造校验和载荷规范化。

## 存储版本与表归属

- `meta.version='1'`：`models_v1/heads_v1/receipts_v1/tasks_v1/results_v1`
  是业务真源（原始行为）。新建空库从版本 '1' 开始，`Store` 初始化不自动升级。
- `meta.version='2'`：对应五张 `*_v2` 表（`vendor.sdk2.SCHEMA`）是业务真源；
  五张 `*_v1` 表逐字冻结，仅供审计，业务读写不再触碰。
- `migration.upgrade(store, before_commit=None)` 在单个 `db.atomic` 事务内：
  安装 `sdk2.SCHEMA`；按 `(id, revision)` 顺序把每个旧修订转换为
  `FactorModel` 并经 `sdk2.append` 写入（由此重建 `heads_v2`）；逐字复制
  receipts/tasks/results；最后把 `meta.version` 改为 `'2'`。`before_commit`
  在全部转换与版本修改之后、提交之前调用一次；异常原样传播并回滚全部修改。
  版本已为 `'2'` 时调用无副作用且不调用钩子；未知版本拒绝且不写库。

## 模型表示不变量

旧表示为均值 μ 与对称半正定协方差 Σ。迁移后的 `FactorModel` 满足：

1. **仿射支撑**：`{x | Hx = h} = μ + range(Σ)`。H 的行是 `ker(Σ)` 的一组
   基（由精确 RREF 求得），`h = Hμ`。硬等式可冗余；零秩 Σ 时支撑为单点 μ。
2. **能量**：支撑内任意 x 满足
   `energy(x) = Σ_i w_i (r_i·x − t_i)^2 = (x−μ)' Σ⁺ (x−μ)`，
   其中 `Σ⁺` 是 Moore-Penrose 逆，`t_i = r_i·μ`，`w_i` 为正有理数。
   转换用精确 LDL 分解：`Σ = F D F'`（F 满列秩）给出
   `Σ⁺ = F (F'F)⁻¹ D⁻¹ (F'F)⁻¹ F'`，再对 `Σ⁺` 做 LDL 得到残差行与权重。
3. **合法性**：受限精度在 `ker(H)` 上正定（sdk2 校验保证）；零方差保持为
   硬等式，绝不用微小正数替代。

因此迁移前后是**同一概率分布**（含全部确定关系）：对任意满足
`x = μ + Bz`（`Σ = BB'`，B 满列秩）的点，`energy(x) = |z|²`。

## 矩恢复（兼容 get_model 与条件分析共用）

把支撑写成 `x = p + Kz`（p 为特解，K 的列是 `ker(H)` 的基），记 R 为残差
行、`W = diag(w)`、t 为目标值，则 z 的精度 `Λ = K'R'WRK` 在 `ker(H)` 上
正定，可精确求逆：

- 均值 `μ = p + K Λ⁻¹ K'R'W (t − Rp)`
- 协方差 `Σ = K Λ⁻¹ K'`

任意合法原生 `FactorModel`（冗余硬等式、残差不能同时为零、单点支撑）都
按同一公式消费，不限于迁移器产生的布局。`get_model` 据此恢复矩并构造
具备原数据接口的 SDK1 值对象（id、revision、names、mean、covariance、
payload()）。

## 条件语义

精确观测 `c·x = v` 只是向硬等式组追加一行：条件分布是同一能量在新支撑
上的限制（观测梯度在仿射支撑上为常数，离散化公式的雅可比因子与 x 无关，
归一化后消去）。增广方程组不相容 ⇒ `ImpossibleObservation`，并原子发布
`Failure`（无任何数值部分结果）；相容的冗余观测不改变支撑与能量，结果
不变。contrast 的均值与完整联合协方差是对条件矩的线性投影，全部为
Fraction，标签顺序与交叉协方差沿用旧契约。

## 任务、结果与回执

- `tasks_v2`/`results_v2` 的行与 v1 逐字相同：固定修订、具名表达式、
  状态（saved/success/impossible）、已发布的完整结果与失败状态都保留。
- `receipts_v2.payload` 是旧 canonical SDK1 JSON，仅用于写入请求的身份
  比较（重放/冲突判定），不作为矩恢复或分析的数据源。
- 旧 `put_model` 在版本 '2' 时把 `(μ, Σ)` 转换为目标因子写入
  `models_v2`，不再写旧表；重放、冲突、修订间隔（head+1）校验行为不变。
- `sdk2.append` 的原生修订没有旧矩载荷与旧回执；旧接口立即看见：
  `head/revisions/get_model` 直接读 v2 表，`get_model` 由因子恢复矩。
  已有任务仍绑定旧修订，新任务按指定修订绑定，变量顺序按各修订自身的
  names 解释，不会串绑。
