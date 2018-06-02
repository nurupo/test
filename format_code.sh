find . -name '*.h' -or -name '*.cpp' -exec clang-format-7 -style=file -i {} \;
