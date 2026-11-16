# 联合观测归档升级契约

本文件与 README.md 共同构成公开契约；README.md 描述完整正常的 v1 行为，本文件说明本轮增量及升级后的覆盖规则。现有程序仍完整使用 SDK1；vendor/sdk2.py 是可运行的离线目标端口，没有模型转换或条件分析实现。所有原测试、双代端口与 samples.py 均须保留，不得更改原断言或替换依赖来绕过升级。

## 共同业务结果与两项交付

A 负责将模型概率分布、修订目录及关联任务状态真实迁入 SDK2 存储，并适配旧写入接口。B 负责让兼容读取及条件分析消费目标因子，保持全部确定关系和联合条件矩。两项共享的交接是固定修订的变量顺序、仿射支撑及支撑内概率结构。只完成 A，旧执行器仍无法独立消费目标因子；只完成 B，历史归档与任务未完成真实迁移。

范围仅为 1..8 维有理联合高斯模型、最多 6 条精确线性观测及 1..8 个有序 contrast；允许奇异和零秩，允许全量重算。无拟合、近似推断、不等式条件、在线迁移、多工作者、性能指标或通用恢复系统。

## 已有格式、入口与样本

模型载荷为 {id, revision, names, mean, covariance}；数值接受 int、Fraction 或有理数字符串。names 是该不可变修订的有序变量名；mean 和对称半正定 covariance 的维度一致。完整校验、错误类型及规范化规则沿用 README.md 和现有接口。
Store.put_model(request_id, model)、get_model(id, revision)、head、revisions、receipt 以及 Analysis.save/run、Tasks.get/status、Results.get 的签名和原有行为须保持。旧 get_model 仍返回具备原 Model 数据接口的值对象，包含 id、revision、names、mean、covariance 及 payload()。SDK1 可仅用于旧 API 值对象的类型兼容、构造校验和载荷规范化；升级后的分布恢复与条件计算不得调用 SDK1.conditional 或 SDK1 的其他概率计算。不得把目标矩恢复成 SDK1 模型再调用旧条件执行器。

samples.build_legacy(path) 直接用冻结 SQL 构造完整旧库，不依赖待实现迁移。MODELS 含 joint 的两个修订（第二修订变量顺序为 y,x）、fixed 及 independent；库中有四个 seed-* 请求回执、已发布 old-task 和未执行 waiting-task。old-task 固定 joint 修订 1，条件 x=3，y 为 6、方差为 0；waiting-task 同修订条件 x=4，y 为 8、方差为 0。这些是已有数据，不得用新建空库替代升级。

## 升级调用和存储边界

新增 migration.upgrade(store, before_commit=None)，store 是已打开的 archive.Store；返回 None。显式调用前继续支持原 v1 工作流，Store 初始化不能自动升级历史库。新建空库也先遵循 v1 行为。

维护窗口只有一个工作者，调用前没有未结束事务。upgrade 将全部 models_v1、heads_v1、receipts_v1、tasks_v1、results_v1 的业务状态迁至 vendor.sdk2.SCHEMA 定义的对应五张 *_v2 表，再将 meta 中 version 从字符串 '1' 原子改为 '2'。目标模型必须实际保存为 FactorModel 载荷，不能把旧矩载荷塞进目标因子字段。任务 payload 的具名表达式和固定修订、状态、已发布完整结果及失败状态均须保留；已有请求的 canonical payload 和原绑定须保留，重放不因 head 增长而改变。

before_commit 无参数，在全部转换、目标写入和版本修改后且提交前调用一次；异常原样传播，并回滚本次全部数据库修改。失败后原库仍能执行，关闭重开后可重试；version 已为 '2' 时重复调用无副作用且不调用 before_commit。未知存储版本拒绝且不修改数据库。复用 db.atomic 即可，不要求新增事务框架或进程终止协议。

全部 v1 业务表逐字保留为审计，成功切换后不再读写它们来提供业务服务。receipts_v2.payload 只用于原写入请求的身份比较，不得作为矩恢复或分析的数据源。成功切换后既有 Store、Analysis、Tasks、Results 和重开对象均以目标表为业务真源，不依赖迁移时缓存。旧 put_model 也须把分布转换为目标因子，不向旧表续写；原请求及任务的重放、冲突和异常发布行为不变。原有结果发布事务仍可复用。

SDK2 原生写入由调用方在 db.atomic(store.connection) 内调用 sdk2.append(connection, factor_model)，只追加 head+1，不制造旧写入请求回执。旧接口必须立即看到原生新修订；已有任务仍绑定旧修订，新任务按指定新修订绑定，即使变量顺序改变也不能串绑。原生修订没有旧矩载荷，兼容 get_model 必须依据其目标因子恢复数据。

## 目标概率分布及条件语义

FactorModel 的字段和函数签名见 vendor/sdk2.py。它在 H*x=h 上表示密度 proportional to exp(-energy(x)/2)，energy 为 sum(w_i*(r_i*x-target_i)^2)，相对于该仿射支撑的欧氏测度；权重是正有理数，受限二次型在 ker(H) 上正定。硬等式可冗余，支撑可以是单点；残差可冗余且无需能同时为零。任意合法原生 FactorModel 均须可消费，不能只支持迁移器自己产生的某一种因子布局。

迁移后的目标因子必须表达与旧均值及协方差相同的概率分布，包括全部确定关系；不得以微小正数替换零方差。
Analysis.run 返回所有 contrast 的条件均值及完整联合条件协方差；冗余且相容的精确观测不改变结果，与模型支撑矛盾的观测抛出 ImpossibleObservation 并原子发布 Failure，不得有部分数值。
所有结果数值为 Fraction；观测 offset、常量表达式、变量名称绑定、contrast 标签顺序和交叉协方差都沿用旧契约。允许重新计算全部有限维状态，不强制直接因子条件化、缓存或额外索引。

手工兼容例：
* names=(x,y)，mean=(1,2)，covariance=((1,2),(2,4))，硬支撑是 y=2x。只观测 x=3 时 y=6 且方差 0；再加 y=6 不变；改加 y=7 须拒绝。
* 完全确定模型 mean=(2,-1)、covariance=0，相容条件不得因零秩拒绝，不相容条件须拒绝。
* SDK2 原生 joint 修订 3：names=(y,x)，hard=((1,1),(2,2))，rhs=(3,6)，residuals=((1,0),(0,1))，targets=(0,0)，weights=(1,2)。其两个残差不能同时为零，但分布合法：y 均值 2，x 均值 1，协方差为 ((1/3,-1/3),(-1/3,1/3))；观测 x=2 后 y=1 且方差 0。不得把残差当硬等式。

## 交付验证与运行

保留全部原回归并交付简短表示不变量说明，以及不以 SDK1 输出为唯一判据的独立精确对照。对照覆盖满秩、秩亏、单点支撑、非零均值、多条件冗余/矛盾、变量换序及多 contrast 相关性。可以复用现有潜变量单观测对照，但须补足本轮目标表示与多条件的独立核对，不要求穷举无限输入。

migration_checks.py 是已提供的公开业务核验材料，不含迁移实现。运行：
    python3.11 -B verify.py
    python3.11 -B demo.py
    python3.11 -B migration_checks.py --fixtures
    python3.11 -B migration_checks.py --verify
    python3.11 -B migration_checks.py --demo

前三条可在初始旧系统使用；--verify 和 --demo 是本轮完成后的真实联合入口。--verify 同时运行全部原测试和新增迁移核验，--demo 使用真实旧库执行失败回滚、重开升级、原生续写和固定任务复跑，输出计算所得 JSON。

保留 .pointwise-runtime.json 的 version=1、kind=cli、setup 为 argv 数组列表、verify/demo 为 argv 数组的格式。完成后将 verify 和 demo 分别接到上述 --verify、--demo 入口；不得以打印固定成功标记替代计算。没有额外安装依赖，全部端口源码已提供。可新增自己的业务核验，但不得削弱或改写原测试。
