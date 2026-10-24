#pragma once
#include <cstring>
#include <cstdio>
#define memcpy_s(d, ds, s, n) (memcpy((d), (s), (n)), 0)
