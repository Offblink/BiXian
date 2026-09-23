@echo off
rem Thin wrapper so `decide` works as a command from cmd.exe / PowerShell.
rem MUST stay CRLF: cmd.exe mis-parses LF-only batch files.
rem Adjust the repo path if this skill folder is copied somewhere else.
python "%~dp0..\..\bin\decide.py" %*
exit /b %ERRORLEVEL%
