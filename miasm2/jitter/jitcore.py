#
# Copyright (C) 2011 EADS France, Fabrice Desclaux <fabrice.desclaux@eads.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
from miasm2.core import asmbloc
from miasm2.core.interval import interval
from miasm2.jitter.csts import *


class JitCore(object):

    "JiT management. This is an abstract class"

    def __init__(self, ir_arch, bs=None):
        """Initialise a JitCore instance.
        @ir_arch: ir instance for current architecture
        @bs: bitstream
        """

        self.ir_arch = ir_arch
        self.bs = bs
        self.known_blocs = {}
        self.lbl2jitbloc = {}
        self.lbl2bloc = {}
        self.log_mn = False
        self.log_regs = False
        self.log_newbloc = False
        self.segm_to_do = set()
        self.job_done = set()
        self.jitcount = 0
        self.addr2obj = {}
        self.addr2objref = {}
        self.blocs_mem_interval = interval()
        self.disasm_cb = None
        self.split_dis = set()

        self.options = {"jit_maxline": 50  # Maximum number of line jitted
                        }

    def set_options(self, **kwargs):
        "Set options relative to the backend"

        self.options.update(kwargs)

    def add_disassembly_splits(self, *args):
        """The disassembly engine will stop on address in args if they
        are not at the block beginning"""
        self.split_dis.update(set(args))

    def remove_disassembly_splits(self, *args):
        """The disassembly engine will no longer stop on address in args"""
        self.split_dis.difference_update(set(args))

    def load(self, arch, attrib):
        "Initialise the Jitter according to arch and attrib"

        raise Exception("DO NOT instanciate JitCore")

    def get_bloc_min_max(self, cur_bloc):
        "Update cur_bloc to set min/max address"

        if cur_bloc.lines:
            cur_bloc.ad_min = cur_bloc.lines[0].offset
            cur_bloc.ad_max = cur_bloc.lines[-1].offset + cur_bloc.lines[-1].l

    def add_bloc_to_mem_interval(self, vm, bloc):
        "Update vm to include bloc addresses in its memory range"

        self.blocs_mem_interval += interval([(bloc.ad_min, bloc.ad_max - 1)])

        vm.reset_code_bloc_pool()
        for a, b in self.blocs_mem_interval:
            vm.add_code_bloc(a, b + 1)

    def jitirblocs(self, label, irblocs):
        """JiT a group of irblocs.
        @label: the label of the irblocs
        @irblocs: a gorup of irblocs
        """

        raise Exception("DO NOT instanciate JitCore")

    def add_bloc(self, b):
        """Add a bloc to JiT and JiT it.
        @b: the bloc to add
        """

        irblocs = self.ir_arch.add_bloc(b, gen_pc_updt = True)
        b.irblocs = irblocs
        self.jitirblocs(b.label, irblocs)

    def disbloc(self, addr, cpu, vm):
        "Disassemble a new bloc and JiT it"

        # Get the bloc
        if isinstance(addr, asmbloc.asm_label):
            addr = addr.offset

        l = self.ir_arch.symbol_pool.getby_offset_create(addr)
        cur_bloc = asmbloc.asm_bloc(l)

        # Disassemble it
        try:
            asmbloc.dis_bloc(self.ir_arch.arch, self.bs, cur_bloc, addr,
                             set(), self.ir_arch.symbol_pool, [],
                             follow_call=False, dontdis_retcall=False,
                             lines_wd=self.options["jit_maxline"],
                             # max 10 asm lines
                             attrib=self.ir_arch.attrib,
                             split_dis=self.split_dis)
        except IOError:
            # vm_exception_flag is set
            pass

        # Logging
        if self.log_newbloc:
            print cur_bloc
        if self.disasm_cb is not None:
            self.disasm_cb(cur_bloc)

        # Check for empty blocks
        if not cur_bloc.lines:
            raise ValueError("Cannot JIT a block without any assembly line")

        # Update label -> bloc
        self.lbl2bloc[l] = cur_bloc

        # Store min/max bloc address needed in jit automod code
        self.get_bloc_min_max(cur_bloc)

        # JiT it
        self.add_bloc(cur_bloc)

        # Update jitcode mem range
        self.add_bloc_to_mem_interval(vm, cur_bloc)

    def jit_call(self, label, cpu, vmmngr):
        """Call the function label with cpu and vmmngr states
        @label: function's label
        @cpu: JitCpu instance
        @vm: VmMngr instance
        """

        fc_ptr = self.lbl2jitbloc[label]
        return self.exec_wrapper(fc_ptr, cpu.cpu, vmmngr.vmmngr)

    def runbloc(self, cpu, vm, lbl):
        """Run the bloc starting at lbl.
        @cpu: JitCpu instance
        @vm: VmMngr instance
        @lbl: target label
        """

        if lbl is None:
            lbl = cpu.get_gpreg()[self.ir_arch.pc.name]

        if not lbl in self.lbl2jitbloc:
            # Need to JiT the bloc
            self.disbloc(lbl, cpu, vm)

        # Run the bloc and update cpu/vmmngr state
        ret = self.jit_call(lbl, cpu, vm)

        return ret

    def blocs2memrange(self, blocs):
        """Return an interval instance standing for blocs addresses
        @blocs: list of asm_bloc instances
        """

        mem_range = interval()

        for b in blocs:
            mem_range += interval([(b.ad_min, b.ad_max - 1)])

        return mem_range

    def __updt_jitcode_mem_range(self, vm):
        """Rebuild the VM blocs address memory range
        @vm: VmMngr instance
        """

        # Reset the current pool
        vm.reset_code_bloc_pool()

        # Add blocs in the pool
        for a, b in self.blocs_mem_interval:
            vm.add_code_bloc(a, b + 1)

    def del_bloc_in_range(self, ad1, ad2):
        """Find and remove jitted bloc in range [ad1, ad2].
        Return the list of bloc removed.
        @ad1: First address
        @ad2: Last address
        """

        # Find concerned blocs
        modified_blocs = set()
        for b in self.lbl2bloc.values():
            if not b.lines:
                continue
            if b.ad_max <= ad1 or b.ad_min >= ad2:
                # Bloc not modified
                pass
            else:
                # Modified blocs
                modified_blocs.add(b)

        # Generate interval to delete
        del_interval = self.blocs2memrange(modified_blocs)

        # Remove interval from monitored interval list
        self.blocs_mem_interval -= del_interval

        # Remove modified blocs
        for b in modified_blocs:
            try:
                for irbloc in b.irblocs:

                    # Remove offset -> jitted bloc link
                    if irbloc.label.offset in self.lbl2jitbloc:
                        del(self.lbl2jitbloc[irbloc.label.offset])

            except AttributeError:
                # The bloc has never been translated in IR
                if b.label.offset in self.lbl2jitbloc:
                    del(self.lbl2jitbloc[b.label.offset])

            # Remove label -> bloc link
            del(self.lbl2bloc[b.label])

        return modified_blocs

    def updt_automod_code(self, vm, addr, size):
        """Remove code jitted in range [addr, addr + size]
        @vm: VmMngr instance
        @addr: Address of modified code in sandbox
        @size: Modification range size (in bits)
        """

        self.del_bloc_in_range(addr, addr + size / 8)
        self.__updt_jitcode_mem_range(vm)

