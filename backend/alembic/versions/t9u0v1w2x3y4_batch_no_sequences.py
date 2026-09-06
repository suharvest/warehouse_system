"""batch_no 取号器表 batch_no_sequences

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
Create Date: 2026-09-06 10:00:00.000000

原来的 generate_batch_no 是「SELECT 当天最大序号 → +1 → INSERT」。并发入库时
每个连接读到的是同一份**已提交**状态（竞争者的 INSERT 还在各自未提交的事务里），
于是所有请求算出同一个 batch_no，一起撞 (batch_no, warehouse_id) 唯一约束；
调用方的 5 次重试只是把同一次读重复 5 遍，一次也躲不开。2026-09-05 在
harvest-pi 上的压测里，并发 5 时 77% 的 stock-in 返回 409，并发 ≥10 时 100%。

本表把取号变成一次原子写：每个 (warehouse_id, day_key) 一行，
UPDATE last_seq = last_seq + 1 由数据库串行化，两个并发请求必然拿到不同的号。
批次号格式仍是 YYYYMMDD-NNN，老数据与语音播报口径不变。

不做数据回填：generate_batch_no 每次取号都会拿当天 batches 里已有的最大后缀做
下限，所以本表为空时也能从历史数据正确续号。
"""
from alembic import context, op
import sqlalchemy as sa


revision = 't9u0v1w2x3y4'
down_revision = 's8t9u0v1w2x3'
branch_labels = None
depends_on = None


def upgrade():
    # offline（--sql）模式下没有真连接可反射，直接渲染 CREATE TABLE。
    if not context.is_offline_mode():
        if 'batch_no_sequences' in sa.inspect(op.get_bind()).get_table_names():
            return
    op.create_table(
        'batch_no_sequences',
        sa.Column('warehouse_id', sa.Integer(), nullable=False, autoincrement=False),
        sa.Column('day_key', sa.String(length=8), nullable=False),
        sa.Column('last_seq', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('warehouse_id', 'day_key'),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_0900_ai_ci',
    )


def downgrade():
    op.drop_table('batch_no_sequences')
