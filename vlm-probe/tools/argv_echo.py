import sys

print([a.encode("unicode_escape").decode() for a in sys.argv[1:]])
print(sys.argv[1:])
