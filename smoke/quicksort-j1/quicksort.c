#include <stdio.h>
#include <stdlib.h>
#include <limits.h>

void swap(int* a, int* b)
__CPROVER_requires(__CPROVER_w_ok(a, sizeof(int)))
__CPROVER_requires(__CPROVER_w_ok(b, sizeof(int)))
__CPROVER_assigns(*a, *b)
__CPROVER_ensures(*a == __CPROVER_old(*b))
__CPROVER_ensures(*b == __CPROVER_old(*a))
                                                      
                                                      
                         
                                          
                                          
{
    int t = *a;
    *a = *b;
    *b = t;
}


/* avocado_spec helpers (partition / quickSort): the array is bounded to 5 elements (indices 0..4).
   The array is read as one by-value struct (AVOCADO_SPEC_V / AVOCADO_SPEC_OLDV) so that a contract
   clause dereferences arr as few times as possible; counting macros only index the struct. */
struct avocado_spec_arr5 { int a[5]; };
#define AVOCADO_SPEC_V(arr) (*(struct avocado_spec_arr5 *)(arr))
#define AVOCADO_SPEC_OLDV(arr) __CPROVER_old(*(struct avocado_spec_arr5 *)(arr))
#define AVOCADO_SPEC_IN(k, low, high) ((low) <= (k) && (k) <= (high))


int partition (int arr[], int low, int high)
__CPROVER_requires(0 <= low && low <= high && high <= 4)
__CPROVER_requires(__CPROVER_is_fresh(arr, 5 * sizeof(int)))
__CPROVER_assigns(__CPROVER_object_upto(arr + low, ((unsigned)high - (unsigned)low + 1u) * sizeof(int)))
__CPROVER_ensures(low <= __CPROVER_return_value && __CPROVER_return_value <= high
  /* the pivot (old arr[high]) sits at the returned index */
  && AVOCADO_SPEC_V(arr).a[__CPROVER_return_value] == AVOCADO_SPEC_OLDV(arr).a[high]
  /* positions <= ret hold values <= pivot, positions > ret hold values > pivot */
  && __CPROVER_forall { int m; (0 <= m && m < 5) ==> (AVOCADO_SPEC_IN(m, low, high) ==> ((m <= __CPROVER_return_value) == (AVOCADO_SPEC_V(arr).a[m] <= AVOCADO_SPEC_OLDV(arr).a[high]))) }
  /* every value now in arr[low..high] was in the old arr[low..high] */
  && __CPROVER_forall { int i; (0 <= i && i < 5) ==> (AVOCADO_SPEC_IN(i, low, high) ==> __CPROVER_exists { int j; (0 <= j && j < 5) && AVOCADO_SPEC_IN(j, low, high) && AVOCADO_SPEC_V(arr).a[i] == AVOCADO_SPEC_OLDV(arr).a[j] }) })
                                                        
                                                                     
                                              
                                                                                  
                                                                          
                                    
           
                            
                                                                                               
  
                                    
           
                            
                                                                                               
  
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
__CPROVER_requires(-1 <= high && high <= 4 && 0 <= low && low <= high + 1)
__CPROVER_requires(__CPROVER_is_fresh(arr, 5 * sizeof(int)))
__CPROVER_assigns(__CPROVER_object_upto(arr + low, ((unsigned)high - (unsigned)low + 1u) * sizeof(int)))
__CPROVER_ensures(
  /* arr[low..high] is sorted */
  __CPROVER_forall { int i; (0 <= i && i < 4) ==> ((low <= i && i < high) ==> AVOCADO_SPEC_V(arr).a[i] <= AVOCADO_SPEC_V(arr).a[i + 1]) }
  /* every value now in arr[low..high] was in the old arr[low..high] */
  && __CPROVER_forall { int i; (0 <= i && i < 5) ==> (AVOCADO_SPEC_IN(i, low, high) ==> __CPROVER_exists { int j; (0 <= j && j < 5) && AVOCADO_SPEC_IN(j, low, high) && AVOCADO_SPEC_V(arr).a[i] == AVOCADO_SPEC_OLDV(arr).a[j] }) })
{

    if (low < high) {

        int i = partition(arr, low, high);

        quickSort(arr, low, i - 1);
        quickSort(arr, i + 1, high);
    }
}