"""The kernel threads, pid_max and /proc of the shell of each persona (tpot_personas.kthreads, render)."""
import json
import re
import unittest

from tests.helpers import base_tree, tmpdir
from tpot_personas import catalog, kthreads, tree
from tpot_personas.identity import draw
from tpot_personas.render import render


class KernelThreadsTest(unittest.TestCase):
    def test_every_persona(self):
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed)
                content = persona.builder(persona, ident)
                threads = kthreads.kernel_threads(persona, ident, content.processes, content.filesystems)
                pids = [e["PID"] for e in threads]
                taken = {p["PID"] for p in content.processes}
                shell = min(p["PID"] for p in content.processes if p["TTY"].startswith("pts/"))
                with self.subTest(persona=persona.id, seed=seed):
                    self.assertEqual(threads[0]["COMMAND"], "[kthreadd]")
                    self.assertEqual((threads[0]["PID"], threads[0]["PPID"]), (2, 0))
                    self.assertEqual(len(pids), len(set(pids)))
                    self.assertFalse(taken & set(pids))
                    # the room below the login shell stays for the session's processes
                    self.assertLess(max(pids), shell - 30)
                    self.assertEqual(pids, sorted(pids))
                    self.assertTrue(all(re.fullmatch(r"\[[^\[\] ]+\]", e["COMMAND"]) for e in threads), threads)
                    self.assertTrue(all(e["PPID"] == 2 for e in threads[1:]))
                    self.assertTrue(10 <= len(threads) <= 1500, len(threads))
                    # the same list on every draw of this identity
                    self.assertEqual(threads, kthreads.kernel_threads(persona, ident, content.processes,
                                                                      content.filesystems))

    def test_names_follow_the_kernel(self):
        def names(pid):
            persona = catalog.by_id(pid)
            ident = draw(persona, 1)
            content = persona.builder(persona, ident)
            return [e["COMMAND"] for e in kthreads.kernel_threads(persona, ident, content.processes,
                                                                    content.filesystems)], ident
        trixie, ident = names("debian-trixie")
        self.assertIn("[kworker/R-rcu_gp]", trixie)
        self.assertIn("[rcu_preempt]", trixie)
        self.assertEqual(sum(1 for n in trixie if re.fullmatch(r"\[ksoftirqd/\d+\]", n)), ident.hardware.cpus)
        self.assertIn("[jbd2/sda1-8]", trixie)
        jammy, _ = names("ubuntu-jammy")
        self.assertIn("[rcu_gp]", jammy)
        self.assertIn("[rcu_sched]", jammy)
        self.assertIn("[rcu_tasks_rude_]", jammy)
        self.assertIn("[idle_inject/0]", jammy)
        centos, _ = names("centos-7")
        self.assertIn("[xfsaild/vda1]", centos)
        self.assertIn("[rcu_bh]", centos)
        self.assertFalse(any("events_highpri" in n or "/R-" in n for n in centos))
        dlink, _ = names("dlink-dir859")
        self.assertIn("[events/0]", dlink)
        self.assertFalse(any(n.startswith("[kworker") for n in dlink))

    def test_a_getty_is_no_login_shell(self):
        """The room below the login shell and /proc/self follow the shell on pts/, not a getty on tty1."""
        from tpot_personas import common
        persona = catalog.by_id("ubuntu-noble")
        ident = draw(persona, 1)
        content = persona.builder(persona, ident)
        shell = next(p for p in content.processes if p["TTY"].startswith("pts/"))
        getty = common.ps_entry("root", shell["PID"] + 50, "/sbin/agetty -o -p -- \\u --noclear - linux", tty="tty1",
                                stat="Ss+")
        processes = content.processes + [getty]
        self.assertFalse(common.is_login_shell(getty))
        threads = kthreads.kernel_threads(persona, ident, processes, content.filesystems)
        self.assertLess(max(e["PID"] for e in threads), shell["PID"] - 30)
        t = base_tree()
        from tpot_personas import render as render_module
        render_module._shell_proc(t, processes)
        self.assertEqual(tree.node_get(tree.find_node(t, "proc/self"), tree.A_TARGET).rsplit("/", 1)[-1],
                         str(shell["PID"]))

    def test_pid_max(self):
        self.assertEqual(kthreads.pid_max(catalog.by_id("ubuntu-noble")), 4194304)
        self.assertEqual(kthreads.pid_max(catalog.by_id("rhel-9")), 4194304)
        self.assertEqual(kthreads.pid_max(catalog.by_id("centos-7")), 32768)
        self.assertEqual(kthreads.pid_max(catalog.by_id("openwrt")), 32768)


class RenderTest(unittest.TestCase):
    def test_runtime_files_and_proc_of_the_shell(self):
        for pid in ("ubuntu-noble", "centos-7", "openwrt"):
            persona = catalog.by_id(pid)
            ident = draw(persona, 2)
            out = tmpdir(self) / pid
            render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=out / "keys")
            listed = json.loads((out / kthreads.KERNEL_THREADS).read_text())
            cfg = (out / "cowrie.cfg").read_text()
            processes = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
            shell = str([p["PID"] for p in processes if p["TTY"].startswith("pts/")][-1])
            fs = tree.load_pickle(out / "fs.pickle")
            with self.subTest(persona=pid):
                self.assertIn(f"\nkernel_threads = {len(listed)}\n", cfg)
                self.assertIn(f"\nkernel_thread_list = {out}/{kthreads.KERNEL_THREADS}\n", cfg)
                self.assertIn(f"\npid_max = {kthreads.pid_max(persona)}\n", cfg)
                self.assertEqual((out / "honeyfs/proc/sys/kernel/pid_max").read_text(), f"{kthreads.pid_max(persona)}\n")
                # /proc/self and /proc/$$ are the login shell of ps
                self.assertEqual(tree.node_get(tree.find_node(fs, "proc/self"), tree.A_TARGET).rsplit("/", 1)[-1], shell)
                self.assertIsNotNone(tree.find_dir(fs, f"proc/{shell}"))
                self.assertIsNone(tree.find_node(fs, "proc/969"))
                self.assertIsNotNone(tree.find_node(fs, f"proc/{shell}/status"))
                link = tree.find_node(fs, "proc/self")
                self.assertEqual(tree.node_get(link, tree.A_SIZE), len(tree.node_get(link, tree.A_TARGET)))


if __name__ == "__main__":
    unittest.main()
