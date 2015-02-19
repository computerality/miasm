import miasm2.expression.expression as m2_expr
from miasm2.expression.simplifications import expr_simp
from miasm2.core import asmbloc
from miasm2.ir.translators.C import TranslatorC
import logging


log_to_c_h = logging.getLogger("ir_helper")
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(levelname)-5s: %(message)s"))
log_to_c_h.addHandler(console_handler)
log_to_c_h.setLevel(logging.WARN)


prefetch_id = []
prefetch_id_size = {}
for size in [8, 16, 32, 64]:
    prefetch_id_size[size] = []
    for i in xrange(20):
        name = 'pfmem%.2d_%d' % (size, i)
        c = m2_expr.ExprId(name, size)
        globals()[name] = c
        prefetch_id.append(c)
        prefetch_id_size[size].append(c)

def init_arch_C(arch):
    arch.id2Cid = {}
    for x in arch.regs.all_regs_ids + prefetch_id:
        arch.id2Cid[x] = m2_expr.ExprId('vmcpu->' + str(x), x.size)

    arch.id2newCid = {}

    for x in arch.regs.all_regs_ids + prefetch_id:
        arch.id2newCid[x] = m2_expr.ExprId('vmcpu->%s_new' % x, x.size)


def patch_c_id(arch, e):
    return e.replace_expr(arch.id2Cid)


def patch_c_new_id(arch, e):
    return e.replace_expr(arch.id2newCid)


mask_int = 0xffffffffffffffff


pre_instr_test_exception = r"""
// pre instruction test exception
if (vm_mngr->exception_flags) {
    %s;
    return;
}
"""


code_exception_fetch_mem_at_instr = r"""
// except fetch mem at instr
if (vm_mngr->exception_flags & EXCEPT_DO_NOT_UPDATE_PC) {
    %s;
    return;
}
"""
code_exception_fetch_mem_post_instr = r"""
// except fetch mem post instr
if (vm_mngr->exception_flags) {
    %s;
    return;
}
"""


code_exception_fetch_mem_at_instr_noautomod = r"""
// except fetch mem at instr noauto
if ((vm_mngr->exception_flags & ~EXCEPT_CODE_AUTOMOD) & EXCEPT_DO_NOT_UPDATE_PC) {
    %s;
    return;
}
"""
code_exception_fetch_mem_post_instr_noautomod = r"""
// except post instr noauto
if (vm_mngr->exception_flags & ~EXCEPT_CODE_AUTOMOD) {
    %s;
    return;
}
"""


code_exception_at_instr = r"""
// except at instr
if (vmcpu->exception_flags && vmcpu->exception_flags > EXCEPT_NUM_UPDT_EIP) {
    %s;
    return;
}
"""

code_exception_post_instr = r"""
// except post instr
if (vmcpu->exception_flags) {
    if (vmcpu->exception_flags > EXCEPT_NUM_UPDT_EIP) {
      %s;
    }
    else {
      %s;
    }
    return;
}
"""


code_exception_at_instr_noautomod = r"""
if ((vmcpu->exception_flags & ~EXCEPT_CODE_AUTOMOD) && vmcpu->exception_flags > EXCEPT_NUM_UPDT_EIP) {
    %s;
    return;
}
"""

code_exception_post_instr_noautomod = r"""
if (vmcpu->exception_flags & ~EXCEPT_CODE_AUTOMOD) {
    if (vmcpu->exception_flags > EXCEPT_NUM_UPDT_EIP) {
      %s;
    }
    else {
      %s;
    }
    return;
}
"""

goto_local_code = r"""
if (BlockDst->is_local) {
    goto *local_labels[BlockDst->address];
}
else {
    return;
}
"""

my_size_mask = {1: 1, 2: 3, 3: 7, 7: 0x7f,
                8: 0xFF,
                16: 0xFFFF,
                32: 0xFFFFFFFF,
                64: 0xFFFFFFFFFFFFFFFFL}

exception_flags = m2_expr.ExprId('exception_flags', 32)


def set_pc(ir_arch, src):
    dst = ir_arch.jit_pc
    if not isinstance(src, m2_expr.Expr):
        src = m2_expr.ExprInt_from(dst, src)
    e = m2_expr.ExprAff(dst, src.zeroExtend(dst.size))
    return e


def gen_resolve_int(ir_arch, e):
    return 'Resolve_dst(BlockDst, %X, 0)'%(e)

def gen_resolve_id_lbl(ir_arch, e):
    if e.name.name.startswith("lbl_gen_"):
        # TODO XXX CLEAN
        return 'Resolve_dst(BlockDst, 0x%X, 1)'%(e.name.index)
    else:
        return 'Resolve_dst(BlockDst, 0x%X, 0)'%(e.name.offset)

def gen_resolve_id(ir_arch, e):
    return 'Resolve_dst(BlockDst, %s, 0)'%(TranslatorC.from_expr(patch_c_id(ir_arch.arch, e)))

def gen_resolve_mem(ir_arch, e):
    return 'Resolve_dst(BlockDst, %s, 0)'%(TranslatorC.from_expr(patch_c_id(ir_arch.arch, e)))

def gen_resolve_other(ir_arch, e):
    return 'Resolve_dst(BlockDst, %s, 0)'%(TranslatorC.from_expr(patch_c_id(ir_arch.arch, e)))

def gen_resolve_dst_simple(ir_arch, e):
    if isinstance(e, m2_expr.ExprInt):
        return gen_resolve_int(ir_arch, e)
    elif isinstance(e, m2_expr.ExprId) and isinstance(e.name,
                                                      asmbloc.asm_label):
        return gen_resolve_id_lbl(ir_arch, e)
    elif isinstance(e, m2_expr.ExprId):
        return gen_resolve_id(ir_arch, e)
    elif isinstance(e, m2_expr.ExprMem):
        return gen_resolve_mem(ir_arch, e)
    else:
        return gen_resolve_other(ir_arch, e)


def gen_irdst(ir_arch, e):
    out = []
    if isinstance(e, m2_expr.ExprCond):
        dst_cond_c = TranslatorC.from_expr(patch_c_id(ir_arch.arch, e.cond))
        out.append("if (%s)"%dst_cond_c)
        out.append('    %s;'%(gen_resolve_dst_simple(ir_arch, e.src1)))
        out.append("else")
        out.append('    %s;'%(gen_resolve_dst_simple(ir_arch, e.src2)))
    else:
        out.append('%s;'%(gen_resolve_dst_simple(ir_arch, e)))
    return out

def Expr2C(ir_arch, l, exprs, gen_exception_code=False):
    id_to_update = []
    out = ["// %s" % (l)]
    out_pc = []

    dst_dict = {}
    src_mem = {}

    prefect_index = {8: 0, 16: 0, 32: 0, 64: 0}
    new_expr = []

    e = set_pc(ir_arch, l.offset & mask_int)
    #out.append("%s;" % patch_c_id(ir_arch.arch, e)))

    pc_is_dst = False
    fetch_mem = False
    set_exception_flags = False
    for e in exprs:
        assert isinstance(e, m2_expr.ExprAff)
        assert not isinstance(e.dst, m2_expr.ExprOp)
        if isinstance(e.dst, m2_expr.ExprId):
            if not e.dst in dst_dict:
                dst_dict[e.dst] = []
            dst_dict[e.dst].append(e)
        else:
            new_expr.append(e)
        # test exception flags
        ops = m2_expr.get_expr_ops(e)
        if set(['umod', 'udiv']).intersection(ops):
            set_exception_flags = True
        if e.dst == exception_flags:
            set_exception_flags = True
            # TODO XXX test function whose set exception_flags

        # search mem lookup for generate mem read prefetch
        rs = e.src.get_r(mem_read=True)
        for r in rs:
            if (not isinstance(r, m2_expr.ExprMem)) or r in src_mem:
                continue
            fetch_mem = True
            index = prefect_index[r.size]
            prefect_index[r.size] += 1
            pfmem = prefetch_id_size[r.size][index]
            src_mem[r] = pfmem

    for dst, exs in dst_dict.items():
        if len(exs) == 1:
            new_expr += exs
            continue
        exs = [expr_simp(x) for x in exs]
        log_to_c_h.debug('warning: detected multi dst to same id')
        log_to_c_h.debug('\t'.join([str(x) for x in exs]))
        new_expr += exs
    out_mem = []

    # first, generate mem prefetch
    mem_k = src_mem.keys()
    mem_k.sort()
    for k in mem_k:
        str_src = TranslatorC.from_expr(patch_c_id(ir_arch.arch, k))
        str_dst = TranslatorC.from_expr(patch_c_id(ir_arch.arch, src_mem[k]))
        out.append('%s = %s;' % (str_dst, str_src))
    src_w_len = {}
    for k, v in src_mem.items():
        src_w_len[k] = v
    for e in new_expr:

        src, dst = e.src, e.dst
        # reload src using prefetch
        src = src.replace_expr(src_w_len)
        if dst is ir_arch.IRDst:
            out += gen_irdst(ir_arch, src)
            continue


        str_src = TranslatorC.from_expr(patch_c_id(ir_arch.arch, src))
        str_dst = TranslatorC.from_expr(patch_c_id(ir_arch.arch, dst))



        if isinstance(dst, m2_expr.ExprId):
            id_to_update.append(dst)
            str_dst = patch_c_new_id(ir_arch.arch, dst)
            if dst in ir_arch.arch.regs.regs_flt_expr:
                # dont mask float affectation
                out.append('%s = (%s);' % (str_dst, str_src))
            else:
                out.append('%s = (%s)&0x%X;' % (str_dst, str_src,
                                                my_size_mask[src.size]))
        elif isinstance(dst, m2_expr.ExprMem):
            fetch_mem = True
            str_dst = str_dst.replace('MEM_LOOKUP', 'MEM_WRITE')
            out_mem.append('%s, %s);' % (str_dst[:-1], str_src))

        if e.dst == ir_arch.arch.pc[ir_arch.attrib]:
            pc_is_dst = True
            out_pc += ["return;"]

    # if len(id_to_update) != len(set(id_to_update)):
    # raise ValueError('Not implemented: multi dst to same id!', str([str(x)
    # for x in exprs]))
    out += out_mem

    if gen_exception_code:
        if fetch_mem:
            e = set_pc(ir_arch, l.offset & mask_int)
            s1 = "%s" % TranslatorC.from_expr(patch_c_id(ir_arch.arch, e))
            s1 += ';\n    Resolve_dst(BlockDst, 0x%X, 0)'%(l.offset & mask_int)
            out.append(code_exception_fetch_mem_at_instr_noautomod % s1)
        if set_exception_flags:
            e = set_pc(ir_arch, l.offset & mask_int)
            s1 = "%s" % TranslatorC.from_expr(patch_c_id(ir_arch.arch, e))
            s1 += ';\n    Resolve_dst(BlockDst, 0x%X, 0)'%(l.offset & mask_int)
            out.append(code_exception_at_instr_noautomod % s1)

    for i in id_to_update:
        if i is ir_arch.IRDst:
            continue
        out.append('%s = %s;' %
                   (patch_c_id(ir_arch.arch, i), patch_c_new_id(ir_arch.arch, i)))

    post_instr = []
    # test stop exec ####
    if gen_exception_code:
        if set_exception_flags:
            if pc_is_dst:
                post_instr.append("if (vm_mngr->exception_flags) { " +
                    "/*pc = 0x%X; */return; }" % (l.offset))
            else:
                e = set_pc(ir_arch, l.offset & mask_int)
                s1 = "%s" % TranslatorC.from_expr(patch_c_id(ir_arch.arch, e))
                s1 += ';\n    Resolve_dst(BlockDst, 0x%X, 0)'%(l.offset & mask_int)
                e = set_pc(ir_arch, (l.offset + l.l) & mask_int)
                s2 = "%s" % TranslatorC.from_expr(patch_c_id(ir_arch.arch, e))
                s2 += ';\n    Resolve_dst(BlockDst, 0x%X, 0)'%((l.offset + l.l) & mask_int)
                post_instr.append(
                    code_exception_post_instr_noautomod % (s1, s2))

        if fetch_mem:
            if l.additional_info.except_on_instr:
                offset = l.offset
            else:
                offset = l.offset + l.l

            e = set_pc(ir_arch, offset & mask_int)
            s1 = "%s" % TranslatorC.from_expr(patch_c_id(ir_arch.arch, e))
            s1 += ';\n    Resolve_dst(BlockDst, 0x%X, 0)'%(offset & mask_int)
            post_instr.append(
                code_exception_fetch_mem_post_instr_noautomod % (s1))

    # pc manip after all modifications
    return out, post_instr, post_instr + out_pc


def label2offset(e):
    if not isinstance(e, m2_expr.ExprId):
        return e
    if not isinstance(e.name, asmbloc.asm_label):
        return e
    return m2_expr.ExprInt_from(e, e.name.offset)


def expr2pyobj(arch, e):
    if isinstance(e, m2_expr.ExprId):
        if isinstance(e.name, asmbloc.asm_label):
            src_c = 'PyString_FromStringAndSize("%s", %d)' % (
                e.name.name, len(e.name.name))
        else:
            src_c = 'PyLong_FromUnsignedLongLong(%s)' % patch_c_id(arch, e)
    else:
        raise NotImplementedError('unknown type for e: %s' % type(e))
    return src_c


def ir2C(ir_arch, irbloc, lbl_done,
    gen_exception_code=False, log_mn=False, log_regs=False):
    out = []
    # print "TRANS"
    # print irbloc
    out.append(["%s:" % irbloc.label.name])
    #out.append(['printf("%s:\n");' % irbloc.label.name])
    assert len(irbloc.irs) == len(irbloc.lines)
    for l, exprs in zip(irbloc.lines, irbloc.irs):
        if l.offset not in lbl_done:
            e = set_pc(ir_arch, l.offset & mask_int)
            s1 = "%s" % TranslatorC.from_expr(patch_c_id(ir_arch.arch, e))
            s1 += ';\n    Resolve_dst(BlockDst, 0x%X, 0)'%(l.offset & mask_int)
            out.append([pre_instr_test_exception % (s1)])
            lbl_done.add(l.offset)

            if log_regs:
                out.append([r'dump_gpregs(vmcpu);'])

            if log_mn:
                out.append(['printf("%.8X %s\\n");' % (l.offset, str(l))])
        # print l
        # gen pc update
        post_instr = ""
        c_code, post_instr, _ = Expr2C(ir_arch, l, exprs, gen_exception_code)
        out.append(c_code + post_instr)
    out.append([goto_local_code ] )
    return out


def irblocs2C(ir_arch, resolvers, label, irblocs,
    gen_exception_code=False, log_mn=False, log_regs=False):
    out = []

    lbls = [b.label for b in irblocs]
    lbls_local = []
    for l in lbls:
        if l.name.startswith('lbl_gen_'):
            l.index = int(l.name[8:], 16)
            lbls_local.append(l)
    lbl_index_min, lbl_index_max = 0, 0
    lbls_index = [l.index for l in lbls if hasattr(l, 'index')]
    lbls_local.sort(key=lambda x:x.index)

    if lbls_index:
        lbl_index_min = min(lbls_index)
        lbl_index_max = max(lbls_index)
        for l in lbls_local:
            l.index -= lbl_index_min

    out.append("void* local_labels[] = {%s};"%(', '.join(["&&%s"%l.name for l in lbls_local])))

    out.append("goto %s;" % label.name)
    bloc_labels = [x.label for x in irblocs]
    assert label in bloc_labels

    lbl_done = set([None])

    for irbloc in irblocs:
        # XXXX TEST
        if irbloc.label.offset is None:
            b_out = ir2C(ir_arch, irbloc, lbl_done, gen_exception_code)
        else:
            b_out = ir2C(
                ir_arch, irbloc, lbl_done, gen_exception_code, log_mn, log_regs)
        for exprs in b_out:
            for l in exprs:
                out.append(l)
        dst = irbloc.dst
        out.append("")

    return out

