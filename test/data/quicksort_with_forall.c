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
__CPROVER_requires(low >= 0)
__CPROVER_requires(high >= low)
__CPROVER_requires(high < INT_MAX - 1)
__CPROVER_requires(__CPROVER_is_fresh(arr, ((size_t)high + 1) * sizeof(int)))
__CPROVER_assigns(__CPROVER_object_whole(arr))
__CPROVER_ensures(__CPROVER_return_value >= low)
__CPROVER_ensures(__CPROVER_return_value <= high)
__CPROVER_ensures(*(arr + __CPROVER_return_value) == __CPROVER_old(*(arr + high)))
__CPROVER_ensures(__CPROVER_forall { int k1; (low <= k1 && k1 < __CPROVER_return_value) ==> *(arr + k1) <= *(arr + __CPROVER_return_value) })
__CPROVER_ensures(__CPROVER_forall { int k2; (__CPROVER_return_value < k2 && k2 <= high) ==> *(arr + k2) > *(arr + __CPROVER_return_value) })
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
__CPROVER_requires(low >= 0)
__CPROVER_requires(high < INT_MAX - 1)
__CPROVER_requires((high >= low) ==> __CPROVER_is_fresh(arr, ((size_t)high + 1) * sizeof(int)))
__CPROVER_assigns(__CPROVER_object_whole(arr))
{

    if (low < high) {
        
        int i = partition(arr, low, high);
        
        quickSort(arr, low, i - 1); 
        quickSort(arr, i + 1, high); 
    }
}