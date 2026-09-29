package SSH

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestTPotHostKeyIsCreatedOnceAndReused(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ssh_host_key")

	first, err := tpotLoadOrCreateHostKey(path)
	require.NoError(t, err)
	info, err := os.Stat(path)
	require.NoError(t, err)
	assert.Equal(t, os.FileMode(0600), info.Mode().Perm())

	second, err := tpotLoadOrCreateHostKey(path)
	require.NoError(t, err)
	assert.True(t, bytes.Equal(first.PublicKey().Marshal(), second.PublicKey().Marshal()))
	assert.Equal(t, "ssh-rsa", second.PublicKey().Type())
}

func TestTPotHostKeyRejectsGarbage(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ssh_host_key")
	require.NoError(t, os.WriteFile(path, []byte("not a key"), 0600))

	_, err := tpotLoadOrCreateHostKey(path)
	assert.Error(t, err)
}
