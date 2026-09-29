package SSH

// T-Pot: persistent SSH host key.
//
// Upstream lets gliderlabs/ssh generate a new host key on every start, so the
// fingerprint changes with each restart. T-Pot keeps one key in the mounted
// key folder instead, in the same format the former T-Pot fork wrote, so
// existing installations keep their fingerprint. Hooked in by tpot.patch.

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"sync"

	"github.com/gliderlabs/ssh"
	log "github.com/sirupsen/logrus"
	gossh "golang.org/x/crypto/ssh"
)

const (
	tpotHostKeyDefault = "./configurations/key/ssh_host_key"
	tpotHostKeyEnv     = "BEELZEBUB_TPOT_HOST_KEY"
)

var (
	tpotHostKeyOnce   sync.Once
	tpotHostKeySigner ssh.Signer
)

// tpotAddHostKey adds the persistent host key, shared by all SSH services, to server.
func tpotAddHostKey(server *ssh.Server) {
	tpotHostKeyOnce.Do(func() {
		path := os.Getenv(tpotHostKeyEnv)
		if path == "" {
			path = tpotHostKeyDefault
		}
		signer, err := tpotLoadOrCreateHostKey(path)
		if err != nil {
			log.Fatalf("T-Pot: failed to load or create SSH host key %s: %v", path, err)
		}
		tpotHostKeySigner = signer
	})
	server.AddHostKey(tpotHostKeySigner)
}

func tpotLoadOrCreateHostKey(path string) (ssh.Signer, error) {
	privateBytes, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
		if err != nil {
			return nil, fmt.Errorf("generate key: %w", err)
		}
		privateBytes = pem.EncodeToMemory(&pem.Block{
			Type:  "RSA PRIVATE KEY",
			Bytes: x509.MarshalPKCS1PrivateKey(privateKey),
		})
		if err := os.WriteFile(path, privateBytes, 0600); err != nil {
			return nil, fmt.Errorf("save key: %w", err)
		}
	} else if err != nil {
		return nil, fmt.Errorf("read key: %w", err)
	}

	signer, err := gossh.ParsePrivateKey(privateBytes)
	if err != nil {
		return nil, fmt.Errorf("parse key: %w", err)
	}
	return signer, nil
}
