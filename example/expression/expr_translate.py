import random

from miasm2.expression.expression import *
from miasm2.expression.expression_helper import ExprRandom
from miasm2.ir.translators import Translator

random.seed(0)

class ExprRandom_OpSubRange(ExprRandom):
    operations_by_args_number = {1: ["-"],
                                 2: ["<<", ">>",],
                                 "2+": ["+", "*", "&", "|", "^"],
                                 }


print "[+] Compute a random expression:"
expr = ExprRandom_OpSubRange.get(depth=8)
print "-> %s" % expr
print

target_exprs = {}
for lang in Translator.available_languages():
    target_exprs[lang] = Translator.to_language(lang).from_expr(expr)

for target_lang, target_expr in target_exprs.iteritems():
    print "[+] Translate in %s:" % target_lang
    print target_expr
    print

print "[+] Eval in Python:"
def memory(addr, size):
    ret = random.randint(0, (1 << size) - 1)
    print "Memory access: @0x%x -> 0x%x" % (addr, ret)
    return ret

for expr_id in expr.get_r(mem_read=True):
    if isinstance(expr_id, ExprId):
        value = random.randint(0, (1 << expr_id.size) - 1)
        print "Declare var: %s = 0x%x" % (expr_id.name, value)
        globals()[expr_id.name] = value

print "-> 0x%x" % eval(target_exprs["Python"])

print "[+] Validate the Miasm syntax rebuilding"
exprRebuild = eval(target_exprs["Miasm"])
assert(expr == exprRebuild)
