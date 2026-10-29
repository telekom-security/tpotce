package glutton

import (
	"errors"
	"net"
	"syscall"
	"testing"
	"time"
)

func requireTransparentSocket(t *testing.T, err error) {
	t.Helper()
	if errors.Is(err, syscall.ENOPROTOOPT) || errors.Is(err, syscall.EOPNOTSUPP) ||
		errors.Is(err, syscall.EPERM) || errors.Is(err, syscall.EACCES) {
		t.Skipf("transparent sockets unavailable in this build kernel/emulator: %v; run the native smoke test", err)
	}
	if err != nil {
		t.Fatal(err)
	}
}

func TestTpotTCPShutdownInterruptsAccept(t *testing.T) {
	listener, err := listenTProxyTCP("tcp4", &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1)})
	requireTransparentSocket(t, err)
	t.Cleanup(func() { listener.Close() })
	accepted := make(chan error, 1)
	go func() {
		_, err := listener.Accept()
		accepted <- err
	}()
	time.Sleep(20 * time.Millisecond)
	closed := make(chan error, 1)
	go func() { closed <- listener.Close() }()
	for _, result := range []<-chan error{closed, accepted} {
		select {
		case err := <-result:
			if err != nil && !errors.Is(err, net.ErrClosed) {
				t.Fatal(err)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("transparent TCP listener blocked shutdown")
		}
	}
}

func TestTpotUDPDeadlineAndShutdown(t *testing.T) {
	listener, err := listenTProxyUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	requireTransparentSocket(t, err)
	t.Cleanup(func() { listener.Close() })
	if err := listener.SetReadDeadline(time.Now().Add(20 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	read := make(chan error, 1)
	go func() {
		_, _, _, _, err := listener.ReadMsgUDP(make([]byte, 1), make([]byte, 128))
		read <- err
	}()
	select {
	case err := <-read:
		if timeout, ok := err.(net.Error); !ok || !timeout.Timeout() {
			t.Fatalf("want deadline error, got %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("transparent UDP listener ignored its deadline")
	}
}
