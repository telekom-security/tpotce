module github.com/nsmfoo/dicompot

go 1.23

require (
	github.com/grailbio/go-dicom v0.0.0-20190117035129-c30d9eaca591
	github.com/mattn/go-colorable v0.1.6
	github.com/sirupsen/logrus v1.6.0
	github.com/snowzach/rotatefilehook v0.0.0-20180327172521-2f64f265f58c
)

require (
	github.com/BurntSushi/toml v0.3.1 // indirect
	github.com/gobwas/glob v0.0.0-20170212200151-51eb1ee00b6d // indirect
	github.com/konsorten/go-windows-terminal-sequences v1.0.3 // indirect
	github.com/mattn/go-isatty v0.0.12 // indirect
	golang.org/x/sys v0.1.0 // indirect
	golang.org/x/text v0.3.8 // indirect
	gopkg.in/natefinch/lumberjack.v2 v2.0.0 // indirect
	gopkg.in/yaml.v2 v2.3.0 // indirect
)

replace github.com/nsmfoo/dicompot => ../dicompot

replace github.com/golang/lint => ../../golang/lint