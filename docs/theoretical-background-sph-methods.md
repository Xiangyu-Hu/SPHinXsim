# Smoothed Particle Hydrodynamics (SPH) 

SPH is a meshless Lagrangian particle method that was 
originally developed for astrophysical problems by Lucy \cite{lucy1977numerical} 
and Gingold and Monaghan \cite{gingold1977smoothed}.
In SPHinXsys, the SPH method is employed to solve the governing equations of
fluid and solid mechanics, as well as their interactions.

## Gradient Approximation

In SPH discretization of the partial differential equation of fluid 
dynamics, the kernel approximation for the gradient operator 
acting on a smooth field $\psi\left({\boldsymbol{\rm r}}\right)$ 
can be expressed through a two-stage approach

$$
\begin{equation}
	\nabla\psi\left(\boldsymbol{\rm r}\right)\approx
	\int_{\Omega}\nabla\psi\left({\boldsymbol{\rm r}^{*}}
	\right)W\left(\boldsymbol{\rm r}-\boldsymbol{\rm r}^{*},
	h\right)d\boldsymbol{\rm r}^{*}=
	-\int_{\Omega}\psi\left({\boldsymbol{\rm r}^{*}}
	\right)\nabla W\left(\boldsymbol{\rm r}-
	\boldsymbol{\rm r}^{*},h\right)
	d\boldsymbol{\rm r}^{*},
\end{equation}
$$

where $W\left(\boldsymbol{\rm r}, h\right)$ is the kernel 
function scaled by the smoothing length $h$.
While the first stage introduces smoothing errors by the 
kernel function, the second stage entails integration 
by parts, assuming the kernel function vanishes at the 
boundary of a compact support.
Through Taylor expansion, for Eq. \eqref{SPHapproximation} 
one can find that the zero-order consistency condition is

$$	
\begin{equation}
	\int_{\Omega}\nabla W\left(\boldsymbol{\rm r}-
	\boldsymbol{\rm r}^{*},h\right)d\boldsymbol{\rm r}^{*}=0,
\end{equation}
$$

and the first-order consistency condition is

$$
\begin{equation}
	-\int_{\Omega}\left(\boldsymbol{\rm r}^{*}-
	\boldsymbol{\rm r}\right)\otimes\nabla W
	\left(\boldsymbol{\rm r}-\boldsymbol{\rm r}^{*},
	h\right)d\boldsymbol{\rm r}^{*}=\mathbf{I},
\end{equation} 
$$

where $\mathbf{I}$ represents the identity matrix.
Zero-order consistency condition allows rewriting the kernel 
approximation in two equivalent forms:

$$
\begin{equation}
	\nabla\psi\left(\boldsymbol{\rm r}\right)=
	\int_{\Omega}\left(\psi\left({\boldsymbol{\rm r}}
	\right) - \psi\left({\boldsymbol{\rm r}^{*}}\right)\right)
	\nabla W\left(\boldsymbol{\rm r}-\boldsymbol{\rm r}^{*},
	h\right)d\boldsymbol{\rm r}^{*} \\
	\equiv 
	-\int_{\Omega}\left(\psi\left({\boldsymbol{\rm r}}
	\right) + \psi\left({\boldsymbol{\rm r}^{*}}\right)\right)
	\nabla W\left(\boldsymbol{\rm r}-\boldsymbol{\rm r}^{*},
	h\right)d\boldsymbol{\rm r}^{*}.
\end{equation}
$$

By introducing particle summation, the first approximation in 
Eq. \eqref{SPHgradientapproximations} can be further 
approximated for an SPH particle $i$ as

$$
\begin{equation}
	\nabla\psi_{i} = \sum_{j}
	\psi_{ij} \nabla W_{ij}V_{j},
\end{equation}
$$

where $V_{j}$ is the volume of the neighbor particles within 
the support, and the particle-pair difference is $\psi_{ij}=
\psi_{i}-\psi_{j}$.
This form is often referred to as a symmetric or non-conservative 
form.
Similarly, the second approximation in Eq. 
\eqref{SPHgradientapproximations} can be also approximated as

$$
\begin{equation}
	\nabla\psi_{i} = -\sum_{j}
	\left(\psi_{i}+\psi_{j}\right) \nabla W_{ij}V_{j},
\end{equation}
$$

where the particle-pair sum is employed.
This form, known as the anti-symmetric or conservative form, 
ensures discrete conservation and is commonly chosen in classical 
SPH methods for discretizing physical conservation laws.

For the non-conservative form of Eq. \eqref{strong}, zero-order 
consistency is automatically satisfied as the particle-pair 
difference is used.
To achieve first-order consistency, one requires that the 
approximation of Eq. \eqref{firstorderconsistencyintegration} 
satisfies

$$
\begin{equation}
	-\sum_{j}\boldsymbol{\rm r}_{ij}\otimes
	\nabla W_{ij}V_{j} = \mathbf{I}.
\end{equation}
$$

To precisely fulfill the above condition, the KGC approach 
\cite{randles1996smoothed}, introducing a correction matrix 
$\mathbf{B}_{i}$ to adjust the gradient of the kernel function, 
can be employed, so that one has 

$$
\begin{equation}
	-\sum_{j}\boldsymbol{\rm r}_{ij}\otimes
	\mathbf{B}_{i}\nabla W_{ij}V_{j}=\mathbf{I}, 
	\quad \mathbf{B}_{i}=\left(-\sum_{j}
	\mathbf{r}_{ij}\otimes\nabla  
	W_{ij}V_{j}\right)^{-1}.
\end{equation}
$$

With the KGC, Eq. \eqref{strong} is modified as 
$$
\begin{equation}
	\nabla\psi_{i} = \sum_{j}
	\psi_{ij} \mathbf{B}_{i}\nabla W_{ij}V_{j}.
\end{equation}
$$

Note that, introducing $\mathbf{B}_{i}$ does not affect
the zero-order consistency of Eq. \eqref{strong-corrected}.
Also note that, although the non-conservation form is not 
desirable for the discretization of physical conservation laws, 
Eq. \eqref{strong-corrected} is often used when the conservation 
is not a primary concern because it can reproduce the linear 
gradient and achieve second-order accuracy.

In the conservative form of Eq. \eqref{weak}, where the 
particle-pair sum other than the difference is used, the 
zero-order consistency condition becomes nontrivial as  

$$
\begin{equation}
	\sum_{j}\nabla W_{ij}V_{j}=0.
\end{equation}
$$

Litvinov et al. \cite{litvinov2015towards} proposed a particle 
relaxation process driven by a constant background pressure 
assuming invariant particle volume.
After the particles are settled down or fully relaxed, Eq. 
\eqref{zeroorderconsistency} is satisfied for the zero-order 
consistency.
To achieve first-order constancy, as a straightforward 
extension for Eq. \eqref{weak}, one may suggest 

$$
\begin{equation}
	\nabla\psi_{i}=-\sum_{j}
	\left(\psi_{i}\mathbf{B'}_{i}+\psi_{j}\mathbf{B'}_{j}\right)
	\nabla W_{ij}V_{j},
\end{equation}
$$

where $\mathbf{B'}_{i}$ and $\mathbf{B'}_{j}$ are some 
correction matrices for particles $i$ and $j$, is able to 
reproduce a linear gradient similar to Eq. \eqref{strong-corrected}.
How to obtain these correction matrices is not straightforward,
and various attempts based on the original KGC matrix for the 
non-conservative form have been carried out.
One widely used formulation, introduced by Oger et al. 
\cite{oger2007improved}, is expressed as,

$$
\begin{equation}
	\nabla\psi_{i}=-\sum_{j}
	\left(\psi_{i}\mathbf{B}_{i}+\psi_{j}\mathbf{B}_{j}\right)
	\nabla W_{ij}V_{j},
\end{equation}
$$

where the KGC matrix is applied for each particle pair separately.