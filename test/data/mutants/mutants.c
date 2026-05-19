#include <stdlib.h>
#include <stdio.h>

int add(int a, int b)
{
    return a + b;
}

int compare(int a, int b) {
    return a < b;
}

int for_loop(int a, int b)
{
    for (int i = 0; i < 10; i++) {
        a + b;
    }
    return 0;
}

int partition (int arr[], int low, int high)
__CPROVER_requires(0 <= low && low <= high && high < 5)
__CPROVER_requires(__CPROVER_is_fresh(arr, (high + 1) * sizeof(int)))
__CPROVER_assigns(__CPROVER_object_whole(arr))
__CPROVER_ensures(low <= __CPROVER_return_value && __CPROVER_return_value <= high)
{
    int pivot = arr[high];
    int i = low - 1; // Mutants: +

    for (int j = low; j <= high - 1; j++) { // Mutants: >, >=, <, ==, !=, +
        
        if (arr[j] <= pivot) { // Mutants: >, >=, <, ==, !=
            
            i++;
            
            swap(&arr[i], &arr[j]);
        }
    }
    
    swap(&arr[i + 1], &arr[high]); // Mutants: -
    return i + 1; // Mutants: -
}

int with_in_body_assume(int a, int b)
__CPROVER_requires(a < 100)
{
    __CPROVER_assume(a < 50 && b < 50);
    return a + b;
}

void main()
{
    printf("Hello, world");
}