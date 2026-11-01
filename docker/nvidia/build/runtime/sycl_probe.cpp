#include <sycl/sycl.hpp>
#include <iostream>

int main() {
    sycl::queue queue{sycl::gpu_selector_v};
    if (queue.get_backend() != sycl::backend::ext_oneapi_cuda) return 2;
    std::cout << "SYCL CUDA device: "
              << queue.get_device().get_info<sycl::info::device::name>() << '\n';
    int *value = sycl::malloc_shared<int>(1, queue);
    *value = 0;
    queue.single_task([=] { *value = 42; }).wait_and_throw();
    const bool ok = *value == 42;
    sycl::free(value, queue);
    std::cout << "GPU kernel: " << (ok ? "PASS" : "FAIL") << '\n';
    return ok ? 0 : 1;
}
