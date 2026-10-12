#include <stdio.h>
#include <stdlib.h>
#include <limits.h>

/* These specs give both functions a whole-object assigns clause (so every
 * index 0..7 has a recorded pre-state) and an explicit FRAME postcondition
 * that pins everything outside [lo,hi] to its old value -- this restores
 * precise framing, so recursive calls cannot disturb the pivot or the other
 * half. The array is a fixed 8 ints.
 *
 * __CPROVER_old only supports a fixed (constant) lvalue, so we snapshot each
 * of the 8 elements at a constant index and select among those 8 scalar
 * snapshots with a value-level conditional -- never __CPROVER_old of a
 * variable index. */
#define AVOCADO_SPEC_OLDAT(a, idx)                                            \
  ((idx) == 0 ? __CPROVER_old((a)[0]) :                                       \
   (idx) == 1 ? __CPROVER_old((a)[1]) :                                       \
   (idx) == 2 ? __CPROVER_old((a)[2]) :                                       \
   (idx) == 3 ? __CPROVER_old((a)[3]) :                                       \
   (idx) == 4 ? __CPROVER_old((a)[4]) :                                       \
   (idx) == 5 ? __CPROVER_old((a)[5]) :                                       \
   (idx) == 6 ? __CPROVER_old((a)[6]) :                                       \
                __CPROVER_old((a)[7]))

/* Everything outside a[lo..hi] is unchanged by the call. */
#define AVOCADO_SPEC_FRAME(a, lo, hi)                                         \
  __CPROVER_forall {                                                          \
    int avocado_spec_f;                                                       \
    (0 <= avocado_spec_f && avocado_spec_f < 8) ==>                           \
      ((avocado_spec_f < (lo) || avocado_spec_f > (hi)) ==>                   \
        (a)[avocado_spec_f] == AVOCADO_SPEC_OLDAT(a, avocado_spec_f))         \
  }

/* Every element of a[lo..hi] after the call is <= some element that was in
 * a[lo..hi] before the call (i.e. the maximum of the range never grows). */
#define AVOCADO_SPEC_UBOUND(a, lo, hi)                                        \
  __CPROVER_forall {                                                          \
    int avocado_spec_k;                                                       \
    (0 <= avocado_spec_k && avocado_spec_k < 8) ==>                           \
      (((lo) <= avocado_spec_k && avocado_spec_k <= (hi)) ==>                 \
        __CPROVER_exists {                                                    \
          int avocado_spec_j;                                                 \
          (0 <= avocado_spec_j && avocado_spec_j < 8) &&                      \
            (((lo) <= avocado_spec_j && avocado_spec_j <= (hi)) &&            \
              (a)[avocado_spec_k] <= AVOCADO_SPEC_OLDAT(a, avocado_spec_j))   \
        })                                                                    \
  }

/* Every element of a[lo..hi] after the call is >= some element that was in
 * a[lo..hi] before the call (i.e. the minimum of the range never shrinks). */
#define AVOCADO_SPEC_LBOUND(a, lo, hi)                                        \
  __CPROVER_forall {                                                          \
    int avocado_spec_m;                                                       \
    (0 <= avocado_spec_m && avocado_spec_m < 8) ==>                           \
      (((lo) <= avocado_spec_m && avocado_spec_m <= (hi)) ==>                 \
        __CPROVER_exists {                                                    \
          int avocado_spec_n;                                                 \
          (0 <= avocado_spec_n && avocado_spec_n < 8) &&                      \
            (((lo) <= avocado_spec_n && avocado_spec_n <= (hi)) &&            \
              (a)[avocado_spec_m] >= AVOCADO_SPEC_OLDAT(a, avocado_spec_n))   \
        })                                                                    \
  }

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


int partition (int arr[], int low, int high)
__CPROVER_requires(0 <= low && low <= high && high < 8)
__CPROVER_requires(__CPROVER_is_fresh(arr, sizeof(int) * 8))
__CPROVER_assigns(__CPROVER_object_upto(arr, sizeof(int) * 8))
__CPROVER_ensures(low <= __CPROVER_return_value && __CPROVER_return_value <= high)
__CPROVER_ensures(arr[low] <= arr[__CPROVER_return_value])
__CPROVER_ensures(arr[high] >= arr[__CPROVER_return_value])
__CPROVER_ensures(
    __CPROVER_forall {
        int k;
        (0 <= k && k < 8)
            ==> ((low <= k && k < __CPROVER_return_value)
                     ==> arr[k] <= arr[__CPROVER_return_value])
    })
__CPROVER_ensures(
    __CPROVER_forall {
        int m;
        (0 <= m && m < 8)
            ==> ((__CPROVER_return_value < m && m <= high)
                     ==> arr[m] > arr[__CPROVER_return_value])
    })
__CPROVER_ensures(AVOCADO_SPEC_FRAME(arr, low, high))
__CPROVER_ensures(AVOCADO_SPEC_UBOUND(arr, low, high))
__CPROVER_ensures(AVOCADO_SPEC_LBOUND(arr, low, high))
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
__CPROVER_requires(high < 8)
__CPROVER_requires(0 <= low)
__CPROVER_requires(low <= high + 1)
__CPROVER_requires(__CPROVER_is_fresh(arr, sizeof(int) * 8))
__CPROVER_assigns(__CPROVER_object_upto(arr, sizeof(int) * 8))
__CPROVER_ensures(
    __CPROVER_forall {
        int avocado_spec_s;
        (0 <= avocado_spec_s && avocado_spec_s < 8)
            ==> ((low <= avocado_spec_s && avocado_spec_s < high)
                     ==> arr[avocado_spec_s] <= arr[avocado_spec_s + 1])
    })
{

    if (low < high) {

        int i = partition(arr, low, high);

        quickSort(arr, low, i - 1);
        quickSort(arr, i + 1, high);
    }
}