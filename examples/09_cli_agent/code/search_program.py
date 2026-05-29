import re

def linear_search(arr, target):
    for i in range(len(arr)):
        if arr[i] == target:
            return i+1
    return -1


def binary_search(arr, target):
    arr.sort()
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid+1
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1