//go:build linux

package glutton

import (
	"errors"
	"net"
	"syscall"
)

// File().Fd() switches the shared socket to blocking mode. Use RawConn so
// deadlines and Close still interrupt Accept/ReadMsgUDP during shutdown.
func transparentSocket(connection syscall.Conn, udp bool) error {
	raw, err := connection.SyscallConn()
	if err != nil {
		return err
	}
	var optionError error
	if err := raw.Control(func(fd uintptr) {
		optionError = syscall.SetsockoptInt(int(fd), syscall.SOL_IP, syscall.IP_TRANSPARENT, 1)
		if optionError == nil && udp {
			optionError = syscall.SetsockoptInt(int(fd), syscall.SOL_IP, syscall.IP_RECVORIGDSTADDR, 1)
		}
	}); err != nil {
		return err
	}
	return optionError
}

func listenTProxyTCP(network string, address *net.TCPAddr) (net.Listener, error) {
	listener, err := net.ListenTCP(network, address)
	if err != nil {
		return nil, err
	}
	if err := transparentSocket(listener, false); err != nil {
		return nil, errors.Join(err, listener.Close())
	}
	return listener, nil
}

func listenTProxyUDP(network string, address *net.UDPAddr) (*net.UDPConn, error) {
	listener, err := net.ListenUDP(network, address)
	if err != nil {
		return nil, err
	}
	if err := transparentSocket(listener, true); err != nil {
		return nil, errors.Join(err, listener.Close())
	}
	return listener, nil
}
