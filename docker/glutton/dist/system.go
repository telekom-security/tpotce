package glutton

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

func countOpenFiles() int {
	out, err := exec.Command("/bin/sh", "-c", fmt.Sprintf("lsof -p %v", os.Getpid())).Output()
	if err != nil {
		log.Fatal(err)
	}
	lines := strings.Split(string(out), "\n")
	return len(lines) - 1
}

func countRunningRoutines() int {
	return runtime.NumGoroutine()
}

func (g *Glutton) startMonitor(quit chan struct{}) {
	ticker := time.NewTicker(10 * time.Second)
	go func() {
		for {
			select {
			case <-quit:
				g.logger.Info("[system  ] Monitoring stopped..")
				ticker.Stop()
				return
			}
		}
	}()
}
