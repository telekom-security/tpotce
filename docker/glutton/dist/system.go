package glutton

import (
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

func countOpenFiles() (int, error) {
	if runtime.GOOS == "linux" {
		if isCommandAvailable("lsof") {
			out, err := exec.Command("/bin/sh", "-c", fmt.Sprintf("lsof -p %d", os.Getpid())).Output()
			if err != nil {
				log.Fatal(err)
			}
			lines := strings.Split(string(out), "\n")
			return len(lines) - 1, nil
		}
		return 0, errors.New("lsof command does not exist. Kindly run sudo apt install lsof")
	}
	return 0, errors.New("Operating system type not supported for this command")
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

func isCommandAvailable(name string) bool {
	cmd := exec.Command("/bin/sh", "-c", "command -v "+name)
	if err := cmd.Run(); err != nil {
		return false
	}
	return true
}
