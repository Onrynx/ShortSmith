import numpy as np
a = np.array([
    [1, 2, 3],
    [4, 5, 6]
])
b = np.array([
    [1, 4],
    [2, 5],
    [3, 6]
])
print(a@b)
d = np.array([
    [1, 2, 3],
    [4, 5, 6],
    [7, 8, 9]
])
c = np.linalg.det(d)
print(c)
