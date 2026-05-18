#include <stdio.h>
#include <stdlib.h>
#include <limits.h>

void swap(int* a, int* b)
__CPROVER_requires(__CPROVER_is_fresh(a, sizeof(int)))
__CPROVER_requires(__CPROVER_is_fresh(b, sizeof(int)))
__CPROVER_assigns(*a, *b)
__CPROVER_ensures(*a == __CPROVER_old(*b))
__CPROVER_ensures(*b == __CPROVER_old(*a))
{
    int t = *a;
    *a = *b;
    *b = t;
}


int partition (int arr[], int low, int high)
__CPROVER_requires(0 <= low && low <= high && high < 10)
__CPROVER_requires(__CPROVER_is_fresh(arr, (high + 1) * sizeof(int)))
__CPROVER_assigns(__CPROVER_object_whole(arr))
__CPROVER_ensures(low <= __CPROVER_return_value && __CPROVER_return_value <= high)
__CPROVER_ensures(arr[__CPROVER_return_value] == __CPROVER_old(arr[high]))
__CPROVER_ensures(__CPROVER_forall {
    int kl;
    (0 <= kl && kl < 10) ==>
        ((low <= kl && kl < __CPROVER_return_value) ==> arr[kl] <= arr[__CPROVER_return_value])
})
__CPROVER_ensures(__CPROVER_forall {
    int kr;
    (0 <= kr && kr < 10) ==>
        ((__CPROVER_return_value < kr && kr <= high) ==> arr[kr] > arr[__CPROVER_return_value])
})
{
    int pivot = arr[high];
    int i = low - 1;

    for (int j = low; j <= high - 1; j++) {

        if (arr[j] <= pivot) {

            i++;

            swap(&arr[i], &arr[j]);
        }
    }

    swap(&arr[i + 1], &arr[high]);
    return i + 1;
}


void quickSort(int arr[], int low, int high)
__CPROVER_requires(0 <= low && high < 10)
__CPROVER_requires(low <= high + 1)
__CPROVER_requires(__CPROVER_is_fresh(arr, (high + 1) * sizeof(int)))
__CPROVER_assigns(__CPROVER_object_whole(arr))
__CPROVER_ensures(__CPROVER_forall {
    int ks;
    (0 <= ks && ks < 10) ==>
        ((low <= ks && ks < high) ==> arr[ks] <= arr[ks + 1])
})
{

    if (low < high) {

        int i = partition(arr, low, high);

        quickSort(arr, low, i - 1);
        quickSort(arr, i + 1, high);
    }
}